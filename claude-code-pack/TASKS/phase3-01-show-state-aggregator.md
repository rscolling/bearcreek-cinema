# phase3-01: Show-state aggregator

## Goal

Convert raw episode playback rows into show-level taste signal, per
ADR-004 ("episodes are noise; show-binges are signal"). The aggregator
reads `episode_watches` + `show_state` and emits at most one new
`TasteEvent` per show per threshold crossing (`BINGE_POSITIVE` /
`BINGE_NEGATIVE`). It is idempotent: running it twice against the same
data produces the same set of events.

This card also lands the **explicit-rating reader** from ADR-013. Rating
events (`RATED_DOWN` / `RATED_UP` / `RATED_LOVE`) are *written* by the
Roku path (phase 5); this card only adds `taste.ratings.latest_for_show`
so the ranker and profile-update code can consume them.

## Prerequisites

- phase1-03 (`episode_watches`, `show_state`, `taste_events` tables)
- phase1-04 (Jellyfin history → `episode_watches`)

## Inputs

- ADR-004 thresholds (all overridable in `config.taste`):
  - `binge_positive_completion_pct` (default 0.75)
  - `binge_positive_window_days` (default 60)
  - `binge_negative_max_episodes` (default 2)
  - `binge_negative_inactivity_days` (default 30)
- `show_state` columns added in phase1-03:
  - `episodes_finished`, `episodes_abandoned`, `episodes_available`
  - `started_at`, `last_playback_at`
  - `last_emitted_event`, `last_emitted_at` — guards against
    re-emission after a threshold has been crossed once.
- ADR-013 — latest-wins rating semantics

## Deliverables

1. `src/archive_agent/taste/aggregator.py`:

   ```python
   class BingeOutcome(BaseModel):
       show_id: str
       action: Literal["emit_positive", "emit_negative", "skip"]
       reason: str
       event: TasteEvent | None = None   # populated when action != skip

   def evaluate_show(
       state: ShowState,
       config: TasteConfig,
       now: datetime,
   ) -> BingeOutcome:
       """Pure function. No DB. Decides whether this show has crossed
       a threshold since the last emission."""

   async def aggregate_all_shows(
       conn: sqlite3.Connection,
       config: TasteConfig,
       *,
       now: datetime | None = None,
   ) -> list[TasteEvent]:
       """Iterate every row in show_state, call evaluate_show, persist
       the resulting TasteEvents via state.queries.taste.insert_event,
       and stamp last_emitted_event / last_emitted_at on show_state.
       Idempotent: safe to run hourly."""
   ```

2. Show-state refresh helper (so the aggregator sees current counts):

   ```python
   async def refresh_show_state(
       conn: sqlite3.Connection,
       show_id: str,
   ) -> ShowState:
       """Recompute episodes_finished / episodes_abandoned /
       last_playback_at from episode_watches. Write the row back.
       Called before evaluate_show and after any episode_watches
       ingest."""
   ```

3. `src/archive_agent/taste/ratings.py` (ADR-013 reader, new):

   ```python
   RATING_KINDS = {
       TasteEventKind.RATED_DOWN,
       TasteEventKind.RATED_UP,
       TasteEventKind.RATED_LOVE,
   }

   async def latest_for_show(
       conn: sqlite3.Connection,
       show_id: str,
   ) -> TasteEvent | None:
       """Return the newest rating event for this show, or None if
       unrated. Reads taste_events filtered by source='roku_api' and
       kind IN RATING_KINDS, ORDER BY timestamp DESC LIMIT 1."""

   async def latest_for_all_shows(
       conn: sqlite3.Connection,
   ) -> dict[str, TasteEvent]:
       """One-shot bulk read — returns {show_id: latest_rating_event}.
       Used by the ranker (phase3-03) to avoid N+1 queries."""
   ```

4. CLI:
   - `archive-agent taste aggregate` — runs `aggregate_all_shows`,
     prints the emitted events
   - `archive-agent taste show <show_id>` — prints `ShowState` +
     latest rating + what `evaluate_show` would return right now

5. Wire it into the loop: `loop.py` calls `aggregate_all_shows` on a
   configurable interval (default every 15 minutes).

6. Tests in `tests/unit/taste/`:
   - `test_evaluate_show.py` — decision table:
     - below threshold → skip
     - crosses positive (>=75% finished within window) → emit_positive
     - already emitted positive → skip (no duplicates)
     - crosses negative (<=2 finished, window expired) → emit_negative
     - `episodes_available == 0` → skip (can't score)
     - season-complete shortcut → emit_positive regardless of pct
   - `test_aggregate_all_shows.py` — end-to-end with fixture DB:
     fake episode_watches for two shows, run, assert correct rows
     in `taste_events` and `show_state.last_emitted_*`.
   - `test_ratings_latest.py` — insert RATED_DOWN, RATED_UP,
     RATED_LOVE for one show in that order; reader returns
     RATED_LOVE (newest wins). Inserting a non-roku_api event with
     the same kind is ignored.

## Done when

- [ ] `archive-agent taste aggregate` is idempotent: running twice
  produces identical `taste_events` row counts
- [ ] Running after a fresh Jellyfin sync converts binge signal into
  at most one event per show
- [ ] `taste.ratings.latest_for_show` returns the newest
  `roku_api`-sourced rating event for a show, or None
- [ ] `mypy --strict` passes
- [ ] Unit tests pass

## Verification commands

```bash
archive-agent jellyfin sync          # populates episode_watches
archive-agent taste aggregate        # emits binge events
sqlite3 $STATE_DB "SELECT kind, count(*) FROM taste_events GROUP BY kind;"
archive-agent taste aggregate        # second run emits zero new rows
archive-agent taste show <show_id>
```

## Out of scope

- Writing ratings from code — that's phase 5 (Roku → phase4 `/rate`
  endpoint → in-process insert). This card only implements the
  **reader**.
- Per-season binge events — still show-level, even for long shows
- Binge decay over time — handled in `profile update` (phase3-05)

## Notes

- "Season complete" shortcut: if `state.episodes_finished` equals
  `state.episodes_available` AND `episodes_available >= 4`, emit
  `BINGE_POSITIVE` immediately. Prevents waiting 60 days on a tight
  8-episode season that the household blew through in a weekend.
- `strength` for `BINGE_POSITIVE`: 0.8. `BINGE_NEGATIVE`: 0.7. These
  are priors — phase3-05 rebalances against rating strengths (ADR-013).
- `last_emitted_event` is the guard against re-emission. It gets
  cleared only if the aggregator *downgrades* a show (positive → negative
  or vice versa) — which happens when `episodes_available` grows and
  the household doesn't keep up.
- Rating events do **not** go through this aggregator. They're inserted
  directly by the Roku write path (phase 5). The aggregator must
  ignore rows with `source='roku_api'` when computing binge state.
