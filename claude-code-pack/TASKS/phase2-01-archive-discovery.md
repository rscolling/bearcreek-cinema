# phase2-01: Archive.org discovery

## Goal

Populate the `candidates` table by querying both Archive.org
collections (`moviesandfilms` and `television`) with quality filters,
mapping results to `Candidate` Pydantic models, and writing via the
phase1-03 query module. Idempotent — re-running updates existing rows
instead of duplicating.

## Prerequisites

- phase1-02 (config: `[archive]` section)
- phase1-03 (state DB + `candidates` table)
- phase1-06 (structlog; every discover run logs a summary)

## Inputs

- `config.archive.{discovery_interval_minutes, min_download_count, year_from, year_to}`
- `internetarchive` Python library (already in pyproject deps)

## Deliverables

1. `src/archive_agent/archive/search.py`:

   ```python
   class ArchiveSearchResult(BaseModel):
       identifier: str
       title: str
       mediatype: str                 # "movies" | "movingimage" | "collection"
       year: int | None
       downloads: int | None          # lifetime download count — quality proxy
       runtime_minutes: int | None    # from "runtime" or parsed from "length"
       subject: list[str]             # genre-ish tags
       description: str

   async def search_collection(
       collection: Literal["moviesandfilms", "television"],
       *,
       min_downloads: int,
       year_from: int,
       year_to: int,
       limit: int | None = None,
   ) -> AsyncIterator[ArchiveSearchResult]:
       """Wraps internetarchive.search_items with our standard query and
       yields normalized results. Pages through the API; respects
       Archive.org's response-size limits (default 50 items/page)."""
   ```

2. `src/archive_agent/archive/discovery.py`:

   ```python
   def search_result_to_candidate(
       result: ArchiveSearchResult,
       *,
       source_collection: Literal["moviesandfilms", "television"],
   ) -> Candidate:
       """Heuristic type classification:
       - source_collection == 'moviesandfilms' → content_type=MOVIE
       - source_collection == 'television' → EPISODE (default) or SHOW
         if the item's file list contains >1 video → phase2-03 handles
         the ambiguous cases; this phase marks them EPISODE
       Genres map from `subject` (lowercase, dedup)."""

   async def discover(
       conn: sqlite3.Connection,
       config: Config,
       collection: Literal["moviesandfilms", "television", "both"] = "both",
       limit: int | None = None,
   ) -> DiscoverResult:
       """Query the collection(s), upsert each result as a Candidate,
       return counts."""

   class DiscoverResult(BaseModel):
       inserted: int
       updated: int
       skipped_quality: int
       skipped_year: int
       by_collection: dict[str, int]
   ```

3. CLI: replace the `discover` stub with the real implementation.
   `archive-agent discover [--collection moviesandfilms|television|both] [--limit N]`.
   Prints `DiscoverResult` as indented JSON.

4. `internetarchive` integration — use `search_items(..., fields=[...])`
   with a fields list that covers every column we map. Adding a field
   later is fine; missing fields fall back to `None`.

5. Tests:
   - `tests/unit/archive/test_discovery.py` — mapping from fixture
     `ArchiveSearchResult` to `Candidate`, genre normalization,
     year-filter rejection path, min_downloads rejection path
   - `tests/unit/archive/test_search.py` — pagination termination:
     iterator stops when `len(results) < page_size`
   - `tests/integration/test_archive_live.py` — one live call per
     collection, gated on `RUN_INTEGRATION_TESTS=1`, `limit=5` so the
     test is fast and polite

## Done when

- [ ] `archive-agent discover --limit 50` populates `candidates` with at
  least one movie and one television row (assuming network is up)
- [ ] Re-running produces `inserted=0` and `updated=N` — idempotent on
  `archive_id`
- [ ] `candidates.status` is `NEW` for every row this phase writes
- [ ] `skipped_quality` / `skipped_year` counters reflect filters
- [ ] Unit + integration tests pass
- [ ] `mypy --strict` passes on `archive/`

## Verification commands

```bash
archive-agent discover --collection moviesandfilms --limit 20
archive-agent discover --collection television --limit 20
sqlite3 $STATE_DB \
  "SELECT content_type, source_collection, COUNT(*) FROM candidates GROUP BY 1, 2"
# → e.g. movie|moviesandfilms|20, episode|television|20

# Idempotence
archive-agent discover --limit 20
# → DiscoverResult inserted=0, updated=20

RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_archive_live.py -v
```

## Out of scope

- TMDb enrichment (phase2-02)
- Episode → show grouping (phase2-03)
- Downloading anything (phase2-04)
- Daemon loop orchestration (phase4/loop.py)

## Notes

- `internetarchive` respects its own throttling. Don't layer another
  rate-limiter on top; honor the `Retry-After` header on 429s instead
  (the library already does this).
- Archive.org's `downloads` field is noisy — a film with 2k downloads
  isn't necessarily "better" than one with 400. Use it as a coarse
  quality floor (default `min_download_count=100`), not a ranking
  signal. Ranking is phase3.
- Genres: `subject` on Archive.org is freeform. Lowercase + dedup is
  fine at this phase; phase3's TF-IDF featurization can canonicalize.
- Runtime: Archive.org's `runtime` field is sometimes a string like
  `"0:96:00"` or `"96 min"`. Parse defensively; on failure leave
  `runtime_minutes=None` and let phase2-02 fill it from TMDb.
