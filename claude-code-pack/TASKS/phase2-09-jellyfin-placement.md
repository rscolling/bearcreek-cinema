# phase2-09: Jellyfin library scan + item-id linkage

## Goal

After the librarian places a file, trigger Jellyfin to scan the right
library so the file appears in the UI, then resolve the Jellyfin
`ItemId` back onto the `candidate` row. That `ItemId` is what the
Roku app needs to deep-link into the Jellyfin player.

## Prerequisites

- phase1-04 (`JellyfinClient`, `list_libraries`, `trigger_library_scan`,
  `list_items`)
- phase2-06 (placement; we trigger scan after a successful move)
- phase1-03 (`candidates.jellyfin_item_id` column)

## Inputs

- Jellyfin `library_id`s for each zone (resolved once at startup,
  cached thereafter)
- `candidates.archive_id`, `title`, `year`, filesystem path of the
  placed file
- Jellyfin API

## Deliverables

1. `src/archive_agent/jellyfin/placement.py`:

   ```python
   class LibraryMap(BaseModel):
       movies: str                    # jellyfin library id for /media/movies
       tv: str                        # for /media/tv
       recommendations: str           # for /media/recommendations (custom library)
       tv_sampler: str                # for /media/tv-sampler (custom library)

   async def resolve_libraries(client: JellyfinClient) -> LibraryMap:
       """Lists libraries, matches by expected folder path, returns
       the map. Caches the result on the client for the process
       lifetime. Raises if an expected library is missing — we don't
       silently skip `/media/recommendations` just because the user
       hasn't created the library yet."""

   async def scan_and_resolve(
       client: JellyfinClient,
       conn: sqlite3.Connection,
       *,
       archive_id: str,
       zone: Zone,
       timeout_s: int = 90,
   ) -> str | None:
       """Trigger a targeted library scan, poll every 2s until the
       scan finishes or timeout elapses, then look up the newly
       indexed item by name+year. Write the resulting Jellyfin ItemId
       onto candidates.jellyfin_item_id. Returns the ItemId, or None
       on timeout."""
   ```

2. Matching strategy — Jellyfin indexes by parsed title/year from the
   folder+filename. To find the item we just placed:

   ```python
   async def _find_item_for_candidate(
       client: JellyfinClient,
       library_id: str,
       candidate: Candidate,
   ) -> JellyfinItem | None:
       """Searches within a library for an item matching
       (title, year) for movies, or (series_name, season, episode)
       for episodes. Uses `SearchTerm` + the library's ParentId."""
   ```

3. Bulk refresh helper — after a batch placement run, trigger one
   scan per affected library (not per file; Jellyfin's scan is
   whole-library anyway).

   ```python
   async def scan_zones(client: JellyfinClient, zones: list[Zone]) -> None: ...
   ```

4. Library setup documentation — the agent doesn't create Jellyfin
   libraries (scope-limited: "never modify Jellyfin's config"). The
   **user** must create two extra libraries at first deploy:
   - "Recommendations" → type Movies → path `/media/recommendations`
   - "TV Sampler" → type TV Shows → path `/media/tv-sampler`

   Put this as a setup checklist in a new file
   `claude-code-pack/JELLYFIN_SETUP.md` (or append to `ENVIRONMENT.md`).

5. CLI:
   - `archive-agent jellyfin scan [--zone ...]` — trigger a scan of
     one or all zones
   - `archive-agent jellyfin resolve <archive_id>` — manually
     re-attempt the item-id resolution for a single candidate

6. Tests:
   - `tests/unit/jellyfin/test_placement.py` — `resolve_libraries`
     against a fixture `Views` response; raises when a required
     library is missing
   - `tests/unit/jellyfin/test_find_item.py` — matching by
     (title, year) picks the right item when multiple have the same
     name across years
   - `tests/integration/test_scan_and_resolve.py` — gated on
     `RUN_INTEGRATION_TESTS=1`; places a known file in a scratch
     subdir of `/media/recommendations`, triggers scan, asserts
     `candidates.jellyfin_item_id` is populated within the timeout

## Done when

- [ ] After `archive-agent librarian place <archive_id>`, running
  `archive-agent jellyfin scan --zone recommendations` makes the
  item appear in Jellyfin's Recommendations library
- [ ] `candidates.jellyfin_item_id` gets populated for newly placed
  items
- [ ] Missing library → clear error, not silent skip
- [ ] Timeout path is handled (returns None, logs a WARN, doesn't
  crash the caller)
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
archive-agent download <archive_id>
archive-agent librarian place <archive_id> --zone recommendations
archive-agent jellyfin scan --zone recommendations
archive-agent jellyfin resolve <archive_id>

sqlite3 $STATE_DB \
  "SELECT archive_id, jellyfin_item_id FROM candidates \
   WHERE archive_id='<archive_id>'"
# → jellyfin_item_id is a 32-char GUID

# Confirm in Jellyfin UI at http://don-quixote:8096 → Recommendations
```

## Out of scope

- Creating libraries in Jellyfin (user-side setup)
- Updating Jellyfin metadata from our end (we only read)
- Watching Jellyfin webhooks / push events for real-time item index
  notifications — polling after a scan is fine for this phase
- Cover art / poster upload into Jellyfin (Jellyfin fetches its own
  from TMDb when it indexes the file)

## Notes

- `trigger_library_scan(library_id)` triggers a targeted scan, but
  Jellyfin's API for that isn't 100% consistent across versions. If
  the targeted POST doesn't exist on 10.11.x, fall back to a
  whole-library `POST /Library/Refresh`. Test once and pick whichever
  path works; document it here.
- Scan completion polling: Jellyfin has a `/ScheduledTasks/Running`
  endpoint that returns active jobs. Poll that until the library-scan
  task is gone. If we can't identify the scan task cleanly, fall
  back to "wait `scan_lead_time_s` seconds, then try resolving up to
  N times with a 2 s backoff." Practical timeout is ~60-90 s on a
  cold library scan.
- Matching edge case: if the user already has the same public-domain
  film in `/media/movies` under a slightly different name (e.g.,
  "*Night of the Living Dead (1968)*" vs "*Night Of The Living Dead
  [1968]*"), the scan may merge or duplicate. `_find_item_for_candidate`
  should search in the **specific** library (`ParentId=<zone_lib>`)
  to avoid cross-library confusion.
- Long-term (phase6), a webhooks-based approach is more elegant. For
  now, post-scan polling is fine — this only runs a few times per day.
