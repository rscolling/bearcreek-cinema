# phase2-02: TMDb metadata enrichment

## Goal

Fill in the blanks Archive.org doesn't provide — canonical genres,
poster URL, runtime, high-quality description — by matching candidates
to TMDb entries. Cache aggressively in SQLite; TMDb's free tier has
rate limits we must not trip.

## Prerequisites

- phase2-01 (candidates to enrich)
- phase1-02 (config: `[tmdb]` section)
- phase1-06 (structlog for per-call logging)

## Inputs

- `config.tmdb.api_key` (SecretStr)
- TMDb v3 REST API — `/search/movie`, `/search/tv`, `/movie/{id}`,
  `/tv/{id}`, `/configuration` (for image base URL)
- `Candidate` rows with `content_type=MOVIE` or `EPISODE` / `SHOW`

## Deliverables

1. `src/archive_agent/metadata/tmdb.py`:

   ```python
   class TmdbClient:
       def __init__(
           self,
           api_key: SecretStr,
           conn: sqlite3.Connection,       # for the cache + /metadata_cache
           timeout: httpx.Timeout = httpx.Timeout(15.0),
       ) -> None: ...

       async def __aenter__(self) -> "TmdbClient": ...
       async def __aexit__(self, *args) -> None: ...

       async def search_movie(self, title: str, year: int | None) -> TmdbMovie | None:
           """Returns the single highest-rank result, or None."""

       async def search_show(self, title: str, year: int | None) -> TmdbShow | None: ...

       async def get_movie(self, tmdb_id: int) -> TmdbMovie: ...
       async def get_show(self, tmdb_id: int) -> TmdbShow: ...

       async def configuration(self) -> TmdbConfiguration:
           """Needed to build poster URLs; cached for 24h."""
   ```

2. `src/archive_agent/metadata/enrich.py`:

   ```python
   async def enrich_candidate(
       candidate: Candidate,
       client: TmdbClient,
   ) -> Candidate:
       """Mutates & returns a candidate with TMDb fields filled where
       Archive.org was missing them. Never overwrites a non-None
       Archive.org value — the user's own library metadata wins if they
       curated it."""

   async def enrich_new_candidates(
       conn: sqlite3.Connection,
       client: TmdbClient,
       *,
       limit: int | None = None,
   ) -> EnrichResult:
       """Runs enrich_candidate over every candidate with status=NEW
       whose poster_url or genres is empty; upserts the result."""
   ```

3. Migration 003: `metadata_cache` table for TMDb request caching.

   ```sql
   CREATE TABLE metadata_cache (
     cache_key TEXT PRIMARY KEY,        -- "search:movie:<title>:<year>" etc.
     body_json TEXT NOT NULL,
     fetched_at TEXT NOT NULL,
     expires_at TEXT NOT NULL           -- ISO-8601 UTC
   );
   CREATE INDEX idx_metadata_cache_expires ON metadata_cache(expires_at);
   ```

   TTLs: searches 14 days, by-id lookups 30 days, configuration 24h.

4. `src/archive_agent/metadata/models.py` — Pydantic models for TMDb
   responses. Use `extra='ignore'` to tolerate schema evolution.
   Cover only the fields we actually use: `id`, `title`/`name`,
   `release_date`/`first_air_date`, `genres` (resolve IDs via
   `/genre/movie/list` and `/genre/tv/list`, cached), `poster_path`,
   `overview`, `runtime` / `episode_run_time`.

5. Rate limiting + retries:
   - Respect `Retry-After` header on 429. Exponential back-off
     otherwise (100 ms → 400 ms → 1.6 s → give up).
   - Concurrency cap: 4 simultaneous requests (semaphore inside the
     client).

6. CLI: `archive-agent metadata enrich [--limit N]`. Prints an
   `EnrichResult` summary.

7. Tests:
   - `tests/unit/metadata/test_tmdb.py` — fixture JSON responses,
     assert our model parses and fields populate correctly
   - `tests/unit/metadata/test_cache.py` — insert/lookup, TTL
     expiration, cache-miss triggers fetch
   - `tests/unit/metadata/test_enrich.py` — don't overwrite existing
     non-None fields; do fill empty ones
   - `tests/integration/test_tmdb_live.py` — real `configuration` call
     and a cheap search, gated on `RUN_INTEGRATION_TESTS=1`

## Done when

- [ ] `archive-agent metadata enrich` populates `genres`, `poster_url`,
  `runtime_minutes`, `description` on NEW candidates
- [ ] Re-running the same enrichment is a no-op (cache hits, no HTTP)
- [ ] Cache expiry works — after TTL, the next call re-fetches
- [ ] 429 handling is correct (test via mocked transport)
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
archive-agent discover --limit 10
archive-agent metadata enrich --limit 10
sqlite3 $STATE_DB \
  "SELECT archive_id, genres, poster_url IS NOT NULL FROM candidates LIMIT 5"

# Cache round-trip
archive-agent metadata enrich --limit 10     # should be fast + no HTTP
sqlite3 $STATE_DB "SELECT COUNT(*) FROM metadata_cache"

RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_tmdb_live.py -v
```

## Out of scope

- Episode-to-show grouping (phase2-03 uses this client but writes its
  own logic)
- Populating poster image files on disk — we store URLs only and the
  FastAPI layer proxies them (phase4-poster endpoint)
- Fanart/backdrop lookup — posters are enough for the Roku grid
- OMDb / Wikidata fallback — TMDb covers >95% of public-domain-era films

## Notes

- Year disambiguation matters more than you'd expect: *The Lost World*
  (1925) and *The Lost World* (1960) are both on Archive.org. Always
  include `primary_release_year` in the search query.
- TMDb's `genre_ids` in search responses are numeric — resolve to
  names via `/genre/movie/list` (cached). Don't ship a hardcoded map;
  TMDb adds genres occasionally.
- Poster URL construction needs the configuration endpoint's
  `images.secure_base_url` + a size suffix (`w342` is the Roku-friendly
  default). Cache the configuration response for 24h.
- Don't log API keys. The header passthrough should go through the
  structlog redactor automatically because of phase1-06, but verify
  manually that `X-Authorization` or query-string `api_key=...` doesn't
  leak into error logs.
