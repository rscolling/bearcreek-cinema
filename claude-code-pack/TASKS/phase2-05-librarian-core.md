# phase2-05: Librarian core — zones + budget

## Goal

The librarian is the disk-budget policy engine. This card lays the
foundation: zone awareness, disk-usage accounting, budget math, and
the audit-log helper. Placement, eviction, and the TV sampler policy
land in phases 06–08 on top of this.

## Prerequisites

- phase1-02 (config: `[paths]` + `[librarian]`)
- phase1-03 (state DB + `librarian_actions` table)
- phase1-06 (structlog)

## Inputs

- `config.paths.{media_movies, media_tv, media_recommendations, media_tv_sampler}`
- `config.librarian.{max_disk_gb, recommendations_ttl_days, tv_sampler_ttl_days}`

## Deliverables

1. `src/archive_agent/librarian/zones.py`:

   ```python
   class Zone(StrEnum):
       MOVIES = "movies"              # user-owned, never auto-evicted
       TV = "tv"                      # committed, slow eviction + grace
       RECOMMENDATIONS = "recommendations"
       TV_SAMPLER = "tv-sampler"

   AGENT_MANAGED: frozenset[Zone] = frozenset(
       {Zone.TV, Zone.RECOMMENDATIONS, Zone.TV_SAMPLER}
   )
   USER_OWNED: frozenset[Zone] = frozenset({Zone.MOVIES})

   def zone_path(zone: Zone, config: Config) -> Path:
       """Map a Zone to the configured filesystem path."""
   ```

2. `src/archive_agent/librarian/budget.py`:

   ```python
   class ZoneUsage(BaseModel):
       zone: Zone
       path: Path
       used_bytes: int
       file_count: int

   class BudgetReport(BaseModel):
       zones: list[ZoneUsage]
       agent_used_bytes: int          # sum of AGENT_MANAGED zones only
       budget_bytes: int              # config.librarian.max_disk_gb * 1e9
       headroom_bytes: int            # budget_bytes - agent_used_bytes
       over_budget: bool

   def scan_zone(zone_path: Path) -> ZoneUsage:
       """Walks the zone directory. Returns total bytes + file count.
       Missing directories are ZoneUsage(used_bytes=0, file_count=0) —
       never raises."""

   def budget_report(config: Config) -> BudgetReport: ...
   ```

3. `src/archive_agent/librarian/audit.py` — helpers for
   `librarian_actions`:

   ```python
   def log_action(
       conn: sqlite3.Connection,
       *,
       action: Literal["download", "promote", "evict", "skip"],
       zone: Zone,
       reason: str,
       archive_id: str | None = None,
       show_id: str | None = None,
       size_bytes: int | None = None,
   ) -> int:
       """Insert a row; return its id. Timestamp is set here (UTC ISO)."""
   ```

4. CLI: replace the `librarian status` stub.
   `archive-agent librarian status` prints a `BudgetReport` as
   indented JSON + a one-line human summary like:

   ```
   Agent-managed: 127.4 GB / 500 GB (25%), 372.6 GB headroom.
   movies: 1,842 files, 3.2 TB  (user-owned, outside budget)
   tv: 41 files, 94.1 GB
   recommendations: 7 files, 14.3 GB
   tv-sampler: 9 files, 18.9 GB
   ```

5. Tests:
   - `tests/unit/librarian/test_zones.py` — zone enum membership,
     path resolution
   - `tests/unit/librarian/test_budget.py` — scan_zone on a tmp_path
     with a known file layout (3 files, known total), budget math
     (headroom, over_budget), missing-path is 0 bytes
   - `tests/unit/librarian/test_audit.py` — `log_action` inserts a
     row with the right shape; timestamps are UTC

## Done when

- [ ] `archive-agent librarian status` prints a correct, human-readable
  report (test on a tmp dir tree with known sizes)
- [ ] `budget_report(cfg)` returns a correct `BudgetReport` regardless
  of whether any zone paths actually exist
- [ ] `log_action` writes well-formed `librarian_actions` rows
- [ ] `mypy --strict` passes on `librarian/`
- [ ] Tests pass

## Verification commands

```bash
# Set up a fake zone tree
mkdir -p /tmp/aa-test/{movies,tv,recommendations,tv-sampler}
dd if=/dev/zero of=/tmp/aa-test/recommendations/f1.mp4 bs=1M count=5
dd if=/dev/zero of=/tmp/aa-test/tv/f2.mp4 bs=1M count=20

ARCHIVE_AGENT_CONFIG=$(mktemp) ...   # config with paths= /tmp/aa-test/*
archive-agent librarian status

sqlite3 $STATE_DB "SELECT COUNT(*) FROM librarian_actions"
```

## Out of scope

- Moving files (phase2-06 placement)
- Deleting files (phase2-07 eviction)
- Promoting samplers (phase2-08)
- Handling cross-filesystem moves efficiently (stdlib `shutil.move`
  is fine; the whole `/media` tree lives on one mount on don-quixote)

## Notes

- `max_disk_gb` is a hard cap on **agent-managed zones only**.
  `/media/movies` (user-owned) is outside the cap entirely — we never
  budget against it and never evict from it. The GUARDRAILS hard-filter
  that zone explicitly.
- `scan_zone` is a simple recursive walk with `Path.stat().st_size`
  summed. Good enough at `O(10^3)` files. If perf becomes a real
  issue, cache `disk_snapshots` rows (that table exists from
  phase1-03).
- Eviction policies (14-day recommendations TTL, 30-day sampler TTL)
  live in phase2-07. This card only exposes budget math.
- Don't crash on permission errors while walking. Log a warning and
  count the file as zero bytes. Jellyfin's Docker container writes
  some files as a different UID than `blueridge` and those surface
  here.
