# phase2-06: Librarian placement — file moves + Jellyfin-friendly naming

## Goal

Place a freshly-downloaded file from phase2-04's staging area into the
right `/media/*` zone with a name Jellyfin recognizes. Also implement
`promote()` for movies (recommendations → movies) and shows
(tv-sampler → tv) so the lifecycle transitions work.

This is the ONLY module that does `shutil.move` under `/media/` —
every other module asks the librarian to place a file (GUARDRAILS).

## Prerequisites

- phase2-04 (downloader produces staged files)
- phase2-05 (librarian core: zones + budget)
- phase1-03 (`candidates.status`, `candidates.jellyfin_item_id`)

## Inputs

- Staged file path (from `downloads.path`)
- `Candidate` with content_type + title + year (+ show fields)
- Target `Zone`

## Deliverables

1. `src/archive_agent/librarian/placement.py`:

   ```python
   class PlaceResult(BaseModel):
       archive_id: str
       zone: Zone
       source_path: Path
       dest_path: Path
       moved: bool                    # False when dry_run or already placed
       size_bytes: int

   def place(
       conn: sqlite3.Connection,
       config: Config,
       *,
       candidate: Candidate,
       source_path: Path,
       zone: Zone,
       dry_run: bool = False,
   ) -> PlaceResult:
       """Move `source_path` into `zone_path(zone)` with a
       Jellyfin-compatible name. Enforces:
       - `zone in AGENT_MANAGED` (USER_OWNED zones reject unless
         promoting from RECOMMENDATIONS → MOVIES with an explicit
         promote call)
       - Budget headroom (raises if the move would exceed
         `max_disk_gb`; check before moving)
       - No existing file at dest_path (append `" (1)"`, `" (2)"` etc.
         to disambiguate)
       Updates `candidates.status` to DOWNLOADED / COMMITTED / SAMPLING
       as appropriate. Writes a `librarian_actions` row with
       action='download'."""

   def promote_movie(
       conn: sqlite3.Connection,
       config: Config,
       candidate: Candidate,
       *,
       dry_run: bool = False,
   ) -> PlaceResult:
       """Move a candidate from /media/recommendations/{Title}/... to
       /media/movies/{Title}/... . Sets status=COMMITTED. Logs
       action='promote'."""

   def promote_show(
       conn: sqlite3.Connection,
       config: Config,
       candidate: Candidate,
       *,
       dry_run: bool = False,
   ) -> PlaceResult:
       """Same shape for shows: /media/tv-sampler → /media/tv."""
   ```

2. Name generation — pure functions with the tests as the spec:

   ```python
   def jellyfin_movie_folder(title: str, year: int | None) -> str:
       """`Sita Sings the Blues (2008)` — Jellyfin prefers title-in-folder."""

   def jellyfin_movie_filename(title: str, year: int | None, ext: str) -> str: ...

   def jellyfin_show_folder(show_title: str) -> str: ...
   def jellyfin_season_folder(season: int) -> str: ...             # "Season 01"
   def jellyfin_episode_filename(
       show_title: str, season: int, episode: int, ep_title: str | None, ext: str
   ) -> str:
       """`Dick Van Dyke Show - S01E03 - Sick Boy and Sore Loser.mp4`"""
   ```

   Sanitize file-system unsafe characters (`:`, `/`, `\`, `?`, `*`,
   `"`, `<`, `>`, `|`, control chars). Collapse multiple spaces.

3. CLI:
   - `archive-agent librarian place <archive_id> [--zone ...] [--dry-run]`
     (mostly for manual testing — the loop calls `place` directly)
   - `archive-agent librarian promote <archive_id> [--dry-run]`
     (picks promote_movie vs promote_show based on content_type)

4. Tests:
   - `tests/unit/librarian/test_naming.py` — every sanitizer edge case
     + full path construction for movies and episodes
   - `tests/unit/librarian/test_placement.py` — `place()` into a
     tmp_path zone; over-budget rejection; duplicate-name
     disambiguation; `dry_run=True` writes nothing; status transitions
     in the DB
   - `tests/unit/librarian/test_promote.py` — movie + show promotion
     round trip; source file is gone after promote; dest exists

## Done when

- [ ] Placing a file from staging lands at the Jellyfin-compatible path
- [ ] `candidates.status` transitions correctly
- [ ] `librarian_actions` gets one row per placement / promote
- [ ] Budget check rejects when the move would exceed `max_disk_gb`
- [ ] `promote_movie` moves recommendations → movies and updates
  status=COMMITTED
- [ ] `promote_show` moves tv-sampler → tv
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
# End-to-end (uses phase2-04 staging)
archive-agent download night_of_the_living_dead --dest /tmp/aa-staging
archive-agent librarian place night_of_the_living_dead --zone recommendations

ls "/media/recommendations/Night of the Living Dead (1968)/"
sqlite3 $STATE_DB \
  "SELECT action, zone, archive_id, reason FROM librarian_actions ORDER BY id DESC LIMIT 5"

archive-agent librarian promote night_of_the_living_dead
ls "/media/movies/Night of the Living Dead (1968)/"
```

## Out of scope

- Evictions (phase2-07)
- Sampler-first TV logic (phase2-08 — uses `place(zone=TV_SAMPLER)`)
- Triggering Jellyfin scan (phase2-09 — a separate concern from the
  physical move)
- Cross-device moves — all `/media/*` is one mount; use `shutil.move`
  and accept that if someone splits mounts later, the move becomes a
  copy+delete and the budget check matters more

## Notes

- Jellyfin's naming rules live in their documentation; the short
  version is "`<Title> (<Year>)/<Title> (<Year>).ext`" for movies and
  "`<Show>/Season XX/<Show> - SxxEyy - <Episode Title>.ext`" for
  episodes. Get this wrong and Jellyfin stores them as "Untitled" in
  the library; you'll know immediately.
- The budget-vs-placement race: check headroom, then move. If two
  concurrent placements both pass the check and together would exceed
  budget, one succeeds and the second gets rejected during its
  second-stage check (after the first move completes). Cheapest
  solution: serialize placements with an asyncio.Lock at the loop
  level. Document it here; phase4-loop implements the lock.
- `promote_movie` must NOT evict anything from `/media/movies` even if
  `/media/movies` is over some accounting cap — that zone is
  user-owned.
- Sanitizer: start with the Python `pathvalidate` library's
  `sanitize_filename` (not in deps yet — adding it needs a small ADR,
  but it avoids a lot of edge-case whack-a-mole). Alternative:
  hand-rolled regex, which is what the task card's fallback expects.
  Either is fine; pick one and document in the PR.
