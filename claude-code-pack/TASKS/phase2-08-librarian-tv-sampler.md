# phase2-08: TV sampler-first policy

## Goal

Implement the sampler-first TV flow from ARCHITECTURE.md:

1. Show approved → download `sampler_episode_count` (default 3)
   episodes into `/media/tv-sampler/{Show}/Season 01/`
2. If `promote_after_n_finished` (default 2) episodes are finished
   within `promote_window_days` (default 14) → **promote**: download
   remainder of Season 1 into `/media/tv/`
3. If Season 1 finishes → enqueue Season 2, then 3, ...
4. If sampler sits ignored for `tv_sampler_ttl_days` (default 30) →
   eviction (phase2-07 already handles this zone)

The sampler-first pattern is why TV doesn't blow the disk budget —
we only commit the full storage cost of a show once the household
has signalled they like it.

## Prerequisites

- phase2-04 (downloader)
- phase2-05 / 06 / 07 (librarian placement + eviction)
- phase1-03 (`show_state`, `taste_events`)
- phase1-04 (Jellyfin watch history populates `episode_watches`)

## Inputs

- `config.librarian.tv.{sampler_episode_count, promote_after_n_finished, promote_window_days}`
- `show_state` rows (episodes_finished, last_playback_at)
- `candidates` rows with `content_type=EPISODE` grouped by `show_id`

## Deliverables

1. `src/archive_agent/librarian/tv_sampler.py`:

   ```python
   class SamplerDecision(BaseModel):
       show_id: str
       action: Literal["start_sampling", "promote", "wait", "evict"]
       reason: str
       episodes_to_download: list[Candidate]   # empty for wait/evict

   def decide_for_show(
       conn: sqlite3.Connection,
       config: Config,
       show_id: str,
       *,
       now: datetime | None = None,
   ) -> SamplerDecision:
       """Pure-ish logic function: reads show_state + candidates,
       returns what should happen next. Does not download or write
       state — that's the responsibility of `step_show`."""

   async def step_show(
       conn: sqlite3.Connection,
       config: Config,
       show_id: str,
       downloader: Downloader,
   ) -> SamplerResult:
       """Executes the decision from `decide_for_show`: kicks off
       downloads via phase2-04, calls librarian.place for each
       completed file into the right zone, updates show_state. Called
       by the daemon loop (phase4/loop.py)."""

   async def step_all_shows(
       conn, config, downloader
   ) -> list[SamplerResult]:
       """Iterate every show_id with candidates, run step_show for
       each. Respects the concurrency semaphore from phase2-04."""
   ```

2. Promotion criteria — exactly as ARCHITECTURE.md specifies:

   ```python
   def should_promote(
       state: ShowState,
       config: LibrarianTvConfig,
       now: datetime,
   ) -> bool:
       """True iff:
       - state has a sampler committed (sampler_episode_count episodes downloaded)
       - AND state.episodes_finished >= promote_after_n_finished
       - AND time between first sampler episode download and last playback
         is <= promote_window_days
       """
   ```

3. Season-N advancement — once Season 1 is fully downloaded and any
   of its episodes is finished, queue Season 2; repeat. Cap at
   `total_episodes_known` to avoid discovering fake new seasons.

4. CLI:
   - `archive-agent tv sample <show_id>` — force-start sampling for a
     specific show (useful while testing + for the Roku /commit path)
   - `archive-agent tv step <show_id>` — run the decision + execution
     loop for one show (debug tool)
   - `archive-agent tv status` — print per-show state: current zone
     (sampler/committed), episodes finished, decision the next
     `step_show` would take

5. Tests:
   - `tests/unit/librarian/test_sampler_decisions.py` — full decision
     table: no state → start_sampling; sampler partial → wait;
     sampler complete + promotion criteria met → promote; sampler
     complete + criteria not met + within window → wait; sampler
     complete + criteria not met + past window → evict
   - `tests/unit/librarian/test_step_show.py` — run `step_show`
     against a fake downloader, assert the right candidates get
     queued and `show_state` / `candidates.status` transitions are
     correct
   - `tests/integration/test_sampler_promotion.py` — end-to-end with
     a fixture show (5+ episodes in candidates), force-sample,
     simulate "episodes finished" by inserting `episode_watches`,
     run step_show, assert Season 1 remainder is now queued

## Done when

- [ ] `archive-agent tv sample <show_id>` downloads exactly
  `sampler_episode_count` episodes into `/media/tv-sampler/`
- [ ] After enough `episode_watches` rows land to cross the promotion
  threshold, `archive-agent tv step <show_id>` moves the show to
  `/media/tv/` and queues the rest of Season 1
- [ ] Promotion past the window threshold doesn't happen (even if
  criteria are otherwise met — the window is a hard gate)
- [ ] Sampler that sits unwatched gets picked up by phase2-07
  eviction, not by this code (no overlap)
- [ ] `show_state` rows stay consistent across `step_show` invocations
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
# Pick a show with episodes already discovered
SHOW=$(sqlite3 $STATE_DB \
  "SELECT show_id FROM candidates WHERE content_type='episode' \
   AND show_id IS NOT NULL GROUP BY show_id \
   ORDER BY COUNT(*) DESC LIMIT 1")

archive-agent tv sample $SHOW
ls /media/tv-sampler/

# Simulate "user watched 2 episodes"
# (phase1-04 history sync does this for real; here we fake it via SQL)
sqlite3 $STATE_DB "..."

archive-agent tv step $SHOW
ls /media/tv/
```

## Out of scope

- Per-show overrides in config (every show gets the same sampler
  config for now)
- Skipping the sampler phase for critically-acclaimed shows
- Handling shows where Season 1 doesn't exist on Archive.org (it just
  never promotes; not a bug)

## Notes

- Sample episode selection: pick the first N episodes where available
  (`season=1, episode=1..N`). If episode 1 isn't on Archive.org,
  slide forward — but log a WARN because something's weird. Random
  or "best-rated" sampling is scope creep for this phase.
- A show's "first sampler episode downloaded" timestamp — store it in
  `show_state.started_at` on start_sampling (that column already
  exists in the schema from phase1-03).
- The window-check is a hard gate on purpose: a household that
  watched 2 episodes a year ago hasn't shown current interest,
  regardless of how strong the signal was at the time.
- Avoid re-downloading already-downloaded episodes when advancing
  Season 1 → Season 2: check `downloads` and `candidates.status`
  first.
