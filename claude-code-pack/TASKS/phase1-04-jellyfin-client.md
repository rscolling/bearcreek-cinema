# phase1-04: Jellyfin client

## Goal

Async Jellyfin REST client that authenticates, fetches user watch history
(both movies and episodes), triggers library scans, and exposes item IDs
needed for Roku deep-linking.

## Prerequisites

- phase1-02 (config)
- phase1-03 (state DB, for writing episode_watches and taste_events)

## Inputs

- Jellyfin API key and user ID from config
- Jellyfin URL from config

## Deliverables

1. `src/archive_agent/jellyfin/client.py` — `JellyfinClient` class:

   ```python
   class JellyfinClient:
       def __init__(self, url: str, api_key: SecretStr, user_id: str): ...

       async def __aenter__(self) -> "JellyfinClient": ...
       async def __aexit__(self, *args) -> None: ...

       async def ping(self) -> JellyfinServerInfo:
           """GET /System/Info/Public — confirms reachability."""

       async def authenticate(self) -> None:
           """Verify API key works. Called once per client lifetime."""

       async def get_user(self, user_id: str | None = None) -> JellyfinUser: ...

       async def list_libraries(self) -> list[JellyfinLibrary]: ...

       async def list_items(
           self,
           library_id: str | None = None,
           include_item_types: list[str] | None = None,
           fields: list[str] | None = None,
           limit: int | None = None,
           start_index: int = 0,
       ) -> JellyfinItemPage: ...

       async def list_items_paginated(...) -> AsyncIterator[JellyfinItem]:
           """Streams through pagination automatically."""

       async def get_item(self, item_id: str) -> JellyfinItem: ...

       async def get_user_data(self, item_id: str) -> JellyfinUserData:
           """Playback position, completion %, favorite status."""

       async def trigger_library_scan(self, library_id: str | None = None) -> None:
           """POST /Library/Refresh. If library_id given, scans just that one."""

       async def raw_get(self, path: str, params: dict | None = None) -> dict:
           """Escape hatch for endpoints not yet wrapped."""
   ```

2. `src/archive_agent/jellyfin/models.py` — Pydantic models for responses:
   - `JellyfinServerInfo`, `JellyfinUser`, `JellyfinLibrary`,
     `JellyfinItem`, `JellyfinUserData`, `JellyfinItemPage`
   - Only model fields we actually use; use `model_config = {'extra':
     'ignore'}` so we don't break on schema additions from Jellyfin

3. `src/archive_agent/jellyfin/history.py` — higher-level history
   extraction:

   ```python
   async def fetch_movie_history(
       client: JellyfinClient,
   ) -> list[MovieWatchRecord]:
       """Fetch all movies in the user's library with playback data."""

   async def fetch_episode_history(
       client: JellyfinClient,
   ) -> list[EpisodeWatchRecord]:
       """Fetch all episodes in the user's library with playback data."""

   async def classify_movie_signal(record: MovieWatchRecord) -> TasteEvent | None:
       """Apply the rules from ARCHITECTURE.md bootstrap section:
       finished + rewatched → strong positive (strength=1.0)
       finished once → positive (strength=0.7)
       >50% abandoned → neutral (None)
       <20% started → negative (strength=0.3 REJECTED)
       never played but in library → weak negative (strength=0.2 REJECTED)
       """

   async def ingest_all_history(
       client: JellyfinClient,
       write_to_db: bool = True,
   ) -> HistoryIngestResult:
       """One-shot: fetch all history, classify movies, write taste_events
       for movies and episode_watches for episodes."""
   ```

   `MovieWatchRecord` and `EpisodeWatchRecord` are intermediate Pydantic
   types that combine `JellyfinItem` + `JellyfinUserData` into a
   flattened structure.

4. CLI integration:
   - `archive-agent health jellyfin` — calls `ping()`, prints result
   - `archive-agent jellyfin users` — lists users (so config can be set
     with a known user_id)
   - `archive-agent jellyfin libraries` — lists libraries
   - `archive-agent history dump [--type movie|show|any] [--since DATE]`
     — prints watch records
   - `archive-agent history sync` — runs `ingest_all_history` and writes
     to DB

5. Tests:
   - `tests/unit/jellyfin/test_models.py` — parse fixture responses
   - `tests/unit/jellyfin/test_history.py` — classification logic on
     fixture records (no network)
   - `tests/integration/test_jellyfin_live.py` — real Jellyfin, gated on
     `RUN_INTEGRATION_TESTS=1`

## Done when

- [ ] `archive-agent health jellyfin` succeeds against a real server
- [ ] `archive-agent jellyfin libraries` lists at least one library
- [ ] `archive-agent history dump --type movie | head -5` prints movies
  with completion %
- [ ] `archive-agent history dump --type show | head -5` prints episodes
- [ ] `archive-agent history sync` runs, inserts rows into
  `taste_events` (movies) and `episode_watches` (episodes)
- [ ] Re-running `history sync` is idempotent (no duplicate rows —
  dedupe on `jellyfin_item_id + timestamp`)
- [ ] Unit tests all pass
- [ ] `mypy --strict` passes

## Verification commands

```bash
archive-agent health jellyfin
# → {"status": "ok", "version": "10.9.8"}

archive-agent jellyfin libraries
# → lists libraries with IDs and names

archive-agent history dump --type movie --since 2025-01-01 | head -10
# → tabular output, one row per movie

archive-agent history sync
# → "Ingested 247 movie events, 1,842 episode watches."

sqlite3 $STATE_DB "SELECT COUNT(*), kind FROM taste_events GROUP BY kind;"
# → shows distribution of finished/rewatched/etc.

pytest tests/unit/jellyfin -v
```

## Out of scope

- Watching for playback events in real-time (later phase)
- Writing anything to Jellyfin (we only read and trigger scans)
- Creating users, collections, or libraries from the agent

## Notes

- Jellyfin's API uses header `X-Emby-Token: <api_key>` for auth.
- Use `httpx.AsyncClient` with a 30-second timeout. Some library-scan
  endpoints can be slow.
- `UserData` is returned embedded in `Items` responses when you request
  `Fields=UserData` — do that in the one call rather than a per-item
  lookup.
- Don't rely on Jellyfin's own "Recommendations" endpoint; it's opaque
  and item-limited. We want raw watch history.
- Idempotent ingestion: use `INSERT OR IGNORE` with a unique index on
  `(jellyfin_item_id, timestamp)` for episodes; for movies use the
  classification result as the natural dedupe key.
