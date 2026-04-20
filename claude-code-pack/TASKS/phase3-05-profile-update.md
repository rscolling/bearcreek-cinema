# phase3-05: Incremental profile updates

## Goal

Evolve the `TasteProfile` over time. Given the current latest profile
and the `TasteEvent`s that have landed since it was built, produce a
new version that incorporates the new signal without churning the
parts that haven't changed.

Runs periodically (default daily) and opportunistically after large
signal spikes (e.g., the weekend after a big binge). Every run
produces a new append-only `taste_profile` row — history is preserved.

## Prerequisites

- phase3-04 (bootstrap produces version=1)
- phase3-01 (show-state aggregator continues feeding taste_events)
- phase1-05/06 (OllamaProvider + llm_calls wiring)

## Inputs

- `CONTRACTS.md` §1 TasteProfile, §2 `LLMProvider.update_profile`
- `config.taste.update_interval_hours` (default 24)
- `config.taste.min_events_since_last_update` (default 5) — don't
  burn an LLM call on trivial deltas
- `config.taste.max_events_per_update` (default 100) — cap the
  prompt size; older events already summarized in the current
  profile don't need to be re-read

## Deliverables

1. `src/archive_agent/taste/update.py`:

   ```python
   class UpdatePlan(BaseModel):
       current_version: int
       events_since_last: list[TasteEvent]
       events_to_send: list[TasteEvent]     # capped/filtered subset
       should_run: bool
       skip_reason: str | None = None

   async def plan_update(
       conn: sqlite3.Connection,
       config: TasteConfig,
       *,
       now: datetime | None = None,
       force: bool = False,
   ) -> UpdatePlan:
       """Decide whether to run. Skip when:
       - No events since last update
       - Below min_events threshold AND last update was recent
       - Rate-limit: last update was < update_interval_hours ago
         (unless force=True)
       """

   async def apply_update(
       conn: sqlite3.Connection,
       provider: LLMProvider,
       plan: UpdatePlan,
   ) -> TasteProfile:
       """Run provider.update_profile(current, events_to_send),
       insert as new version (current_version + 1), return it."""

   async def run_if_due(
       conn: sqlite3.Connection,
       provider: LLMProvider,
       config: TasteConfig,
   ) -> TasteProfile | None:
       """Convenience: plan + apply if should_run. Returns None when
       skipped. Called by the daemon loop (loop.py)."""
   ```

2. Event selection logic for `events_to_send`:
   - Always include all events newer than `current_profile.updated_at`
   - Apply rating special-case (ADR-013): for each show_id in the
     delta, keep only the **newest** rating event — the others are
     superseded by latest-wins
   - Cap at `max_events_per_update` (newest first) to control prompt
     size
   - If the cap truncates, emit a WARN log with the count dropped —
     a user who's watched 500 things since last update has enough
     signal that any 100 will produce a decent profile, but we want
     it to show up in logs

3. Profile ID preservation helper:

   ```python
   def preserve_ids(
       old: TasteProfile, new: TasteProfile, events: list[TasteEvent]
   ) -> TasteProfile:
       """The LLM sometimes drops liked_archive_ids / liked_show_ids
       from its output. Union old lists with any newly referenced
       IDs from the events (positive kinds → add, negative → remove).
       Never lose explicit ratings — RATED_LOVE / RATED_UP show_ids
       must be in liked_show_ids; RATED_DOWN show_ids in disliked."""
   ```

   Called after `provider.update_profile` returns, before insert.

4. CLI:
   - `archive-agent taste update [--force] [--dry-run]` — runs
     `plan_update` + `apply_update` (or skips with reason); prints
     before/after summary diffs
   - `archive-agent taste history [--limit 10]` — list recent
     profile versions with timestamps + summary snippet

5. Loop integration in `loop.py`:
   - New scheduled task: every `update_interval_hours`, call
     `run_if_due`. Emit a structlog event regardless of outcome.

6. Tests in `tests/unit/taste/`:
   - `test_plan_update.py` — decision table: no events → skip;
     below threshold → skip; interval not elapsed → skip;
     force=True overrides all skips; cap truncates correctly
   - `test_apply_update.py` — fake LLMProvider returns a profile
     missing some liked IDs; `preserve_ids` adds them back
   - `test_rating_preservation.py` — three rating events for the
     same show in the delta; only the newest ends up in
     `events_to_send` and the show lands in the correct liked /
     disliked list per `preserve_ids`

7. Integration test (opt-in): real Ollama, real delta, asserts
   version increments and summary prose differs from prior version.

## Done when

- [ ] `archive-agent taste update --dry-run` shows a diff
- [ ] `archive-agent taste update` inserts version N+1 when due
- [ ] Liked/disliked IDs are never silently dropped
- [ ] Ratings from ADR-013 deterministically land in the right list
- [ ] Loop runs the update on schedule without manual intervention
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
archive-agent taste update --dry-run
archive-agent taste update
archive-agent taste history --limit 5
sqlite3 $STATE_DB "SELECT version, updated_at, length(summary) FROM taste_profile ORDER BY version DESC LIMIT 5;"
```

## Out of scope

- Retrograde profile rewriting (re-synthesizing history). We only
  go forward — old profile rows stay as-is.
- Per-viewer profiles — ADR-007 (single household profile). Phase 6.
- Auto-tuning of the update cadence — fixed interval is fine for v1.

## Notes

- Append-only `taste_profile` rows are cheap (O(KB) each) and
  provide a great debugging trail. Don't prune history, ever.
- The LLM's job here is narrower than bootstrap's: it's evolving
  a summary, not writing it from scratch. The prompt should make
  that explicit so we get minimal edits, not ground-up rewrites
  every time.
- Rating deduplication in `events_to_send` matters because rating
  flipping (👎 → 👍 → 👎) produces multiple rows but only the
  latest reflects current intent. The profile prompt should never
  see both.
- If `provider.update_profile` raises despite Protocol contract
  saying it shouldn't — treat as a bug in the provider but don't
  crash the loop. Log ERROR, skip this cycle, retry next cycle.
