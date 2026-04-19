# phase2-07: Librarian eviction

## Goal

Bring agent-managed disk usage back under `max_disk_gb` when we go
over, using the policies from ARCHITECTURE.md:

1. `/media/recommendations` untouched 14+ days → delete oldest first
2. `/media/tv-sampler` untouched 30+ days → delete
3. **Never** evict from `/media/movies` (user-owned)
4. Committed `/media/tv` shows require an audit-row + grace-period
   before deletion — **never surprise-delete**
5. If still over budget after 1–3, notify the user (log an `eviction
   blocked` event); don't touch committed content silently

## Prerequisites

- phase2-05 (budget report, `Zone` enum, `log_action` helper)
- phase2-06 (knowledge of placed files + paths via `candidates`)
- phase1-03 (`librarian_actions` table for audit + grace)

## Inputs

- `config.librarian.{max_disk_gb, recommendations_ttl_days, tv_sampler_ttl_days}`
- Filesystem timestamps (atime / mtime) + `candidates.status`

## Deliverables

1. `src/archive_agent/librarian/eviction.py`:

   ```python
   class EvictionPlan(BaseModel):
       would_free_bytes: int
       items: list[EvictionItem]      # ordered by priority
       still_over_budget: bool
       blocked_reason: str | None

   class EvictionItem(BaseModel):
       path: Path
       zone: Zone
       archive_id: str | None
       show_id: str | None
       size_bytes: int
       reason: Literal[
           "recommendation_untouched", "sampler_untouched", "sampler_failed_promotion",
       ]
       last_touched_at: datetime

   def plan_eviction(
       conn: sqlite3.Connection,
       config: Config,
       *,
       now: datetime | None = None,   # injectable for tests
   ) -> EvictionPlan:
       """Walk recommendations + tv-sampler zones, collect items that
       satisfy the TTL policies, sort oldest-first, stop collecting
       when cumulative freed bytes would bring usage under budget."""

   def execute_eviction(
       plan: EvictionPlan,
       conn: sqlite3.Connection,
       *,
       dry_run: bool = False,
   ) -> EvictionResult:
       """Delete each item, write a `librarian_actions` row per
       deletion (action='evict', reason=item.reason). Updates
       `candidates.status = EXPIRED` where appropriate. If the plan
       has `still_over_budget=True`, logs a single `eviction_blocked`
       structlog event at WARN level."""
   ```

2. "Touched" semantics — use `last_playback_at` where available
   (from show_state for shows, or a tracked atime for movies), else
   fall back to the candidate's `discovered_at`. Touched = max of
   these timestamps. Implemented in:

   ```python
   def last_touched_at(conn, candidate: Candidate) -> datetime: ...
   ```

3. Committed-TV safety: even if this phase's plan doesn't include
   committed `/media/tv`, leave a stub function that **future phases**
   will wire:

   ```python
   def propose_committed_tv_eviction(
       conn, show_id: str, *, grace_days: int
   ) -> None:
       """Writes a librarian_actions row with action='skip' and
       reason='committed_eviction_proposed'. The user (or phase6
       review UI) approves/rejects. Execution is explicit in a later
       phase — never automatic."""
   ```

4. CLI — replace the `librarian evict` stub:

   ```
   archive-agent librarian evict [--dry-run]
   ```

   Prints the plan. With `--dry-run`, stops there. Without, executes
   and prints the result.

5. Tests:
   - `tests/unit/librarian/test_eviction_plan.py` — item selection
     ordering (oldest first), TTL cutoffs, "never touch movies",
     "stop when budget satisfied"
   - `tests/unit/librarian/test_eviction_execute.py` — dry_run writes
     nothing; non-dry actually deletes; status→EXPIRED; audit row per
     deletion
   - `tests/unit/librarian/test_eviction_blocked.py` — when only
     committed content remains above budget, the plan reports
     `still_over_budget=True` and execute_eviction logs an
     `eviction_blocked` WARN

## Done when

- [ ] `archive-agent librarian evict --dry-run` produces a correct
  `EvictionPlan` on a tree where recommendations and sampler paths
  exist with known mtimes
- [ ] Running without `--dry-run` actually deletes + updates
  status=EXPIRED + writes audit rows
- [ ] `/media/movies` is never in the plan regardless of input state
  (hard-filter test)
- [ ] `/media/tv` is not deleted without an explicit propose+execute
  path
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
# Fake pressure
dd if=/dev/zero of=/media/recommendations/stale.mp4 bs=1M count=5000
touch -d "30 days ago" /media/recommendations/stale.mp4

archive-agent librarian evict --dry-run
# → plan includes stale.mp4

archive-agent librarian evict
ls /media/recommendations/

sqlite3 $STATE_DB \
  "SELECT action, archive_id, size_bytes, reason \
   FROM librarian_actions WHERE action='evict' ORDER BY id DESC LIMIT 5"
```

## Out of scope

- User-approval UI for committed-TV eviction (phase6)
- Paths moved to cold-storage / Glacier-style archival
- Per-genre TTL overrides — single TTL for the zone is enough until
  phase6 feedback argues otherwise

## Notes

- `atime` (access time) is unreliable on ext4 with `noatime` mount
  options (default on most modern Linux). Use `last_playback_at` from
  the Jellyfin/show_state path as the authoritative "touched"
  timestamp, with filesystem `mtime` as a fallback only. Never use
  `atime`.
- The "still over budget" path is critical and must be **loud** —
  emit a WARN log, include the overage size, and include a hint
  pointing at `archive-agent librarian status`.
- Grace period for committed TV isn't implemented here (deferred).
  When it lands, the design is: `action='propose'` row goes in;
  grace period (e.g., 7 days) passes with no user override;
  `action='evict'` row with `reason='committed_after_grace'` follows.
- Don't wrap deletion in a try/except that swallows errors. If a
  delete fails (permission, busy file), log, skip the item, and
  continue with the rest of the plan. The partial progress is better
  than aborting.
