# phase2-04: Archive.org downloader

## Goal

Download candidate items from Archive.org into a temporary staging
area, with resume support, MP4 preference, and progress persisted in
the `downloads` table. Uses `ia-get` (Rust binary, handles resume and
checksums well) with a Python `internetarchive` fallback when the
binary isn't present.

## Prerequisites

- phase2-01 (candidates to download)
- phase1-03 (`downloads` table + queries)
- phase1-06 (structlog for progress + error logging)

## Inputs

- `candidates.archive_id`
- `candidates.formats_available` (MP4 preference order comes from here)
- `config.librarian.max_concurrent_downloads`
- `config.librarian.max_bytes_in_flight_gb`
- `ia-get` binary on PATH (optional) OR the `internetarchive` library

## Deliverables

1. `src/archive_agent/archive/downloader.py`:

   ```python
   class DownloadRequest(BaseModel):
       archive_id: str
       preferred_formats: list[str] = Field(
           default_factory=lambda: ["h.264", "mpeg4", "matroska", "ogg video"]
       )
       dest_dir: Path                 # staging dir, NOT a /media zone
       dry_run: bool = False

   class DownloadResult(BaseModel):
       archive_id: str
       status: Literal["done", "failed", "aborted", "skipped"]
       file_path: Path | None
       size_bytes: int | None
       format: str | None
       duration_s: float
       error: str | None = None

   async def download_one(
       req: DownloadRequest,
       conn: sqlite3.Connection,
   ) -> DownloadResult:
       """Inserts a `downloads` row (status=queued), runs ia-get or the
       Python fallback, streams progress updates via `update_progress`,
       returns the final result."""
   ```

2. Backend dispatch:

   ```python
   async def _download_with_ia_get(req, conn, row_id) -> DownloadResult: ...
   async def _download_with_library(req, conn, row_id) -> DownloadResult: ...

   def _select_backend() -> Literal["ia_get", "library"]:
       """Prefer ia-get if on PATH and --version works; fall back to
       the `internetarchive` Python library otherwise."""
   ```

   `ia-get` invocation uses `--output-dir`, `--resume`, `--quiet`,
   `--include=<glob>`. Parse stdout for progress (ia-get emits
   structured progress lines) OR poll file size every 500 ms. The
   library fallback uses `internetarchive.download(identifier,
   files=[...], destdir=...)` and wraps with size-based progress.

3. Format selection:

   ```python
   def pick_format(files: list[dict[str, Any]], preferred: list[str]) -> dict | None:
       """From Archive.org's item metadata `files` list, choose the
       best match for Roku playback. Walks `preferred` in order;
       returns the first match. Filters out non-video formats, thumbs,
       and derivative files (look for 'derivation' field or naming
       conventions)."""
   ```

4. Concurrency governor — module-level `asyncio.Semaphore` sized by
   `config.librarian.max_concurrent_downloads`, plus a byte-budget
   guard so we don't start a new download if adding its `size_bytes`
   would push active transfer size above
   `max_bytes_in_flight_gb`.

5. CLI: replace the `download` stub with the real impl.
   `archive-agent download <archive_id> [--dry-run] [--dest DIR]`.
   Streams progress to stdout (one compact line per 5% or every 2 s).

6. Tests:
   - `tests/unit/archive/test_downloader.py` — `pick_format` with
     fixture file lists (MP4-first, MKV-fallback, no-video → None),
     backend selection logic (monkeypatched `shutil.which`)
   - `tests/unit/archive/test_downloads_logging.py` — start-fail-retry
     round trip with a mocked backend; asserts `downloads` rows land
     with the right status transitions
   - `tests/integration/test_download_live.py` — download one small
     known-good item (~10 MB — pick a short PD short film), assert
     file exists + correct size, gated on `RUN_INTEGRATION_TESTS=1`

## Done when

- [ ] `archive-agent download <movie-id>` fetches the preferred format
  to the staging dir
- [ ] `downloads` row goes through `queued → downloading → done`
  (or `failed`) with correct timestamps
- [ ] Failed downloads leave a `failed` row with `error` populated and
  do NOT poison a subsequent retry
- [ ] Re-running `download` on a completed `archive_id` sees the
  existing `done` row and returns `status=skipped` without re-downloading
- [ ] Format preference picks MP4 over MKV over WEBM per the default list
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
# Small public-domain short for testing (pick something under 100 MB)
archive-agent download night_of_the_living_dead --dest /tmp/aa-staging

sqlite3 $STATE_DB \
  "SELECT archive_id, zone, status, size_bytes, finished_at \
   FROM downloads ORDER BY id DESC LIMIT 5"

RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_download_live.py -v
```

## Out of scope

- Placing the downloaded file into a `/media/*` zone (phase2-06)
- Triggering Jellyfin scan (phase2-09)
- TV sampler-first logic (phase2-08)
- Downloading whole collections / playlists

## Notes

- `ia-get` is a Rust binary; if it isn't installed on the dev laptop
  the test suite should skip ia-get-specific tests cleanly (use
  `pytest.importorskip`-equivalent for the `shutil.which("ia-get")`
  check).
- `internetarchive.download` is blocking-sync. Wrap it in
  `asyncio.to_thread` so it doesn't stall the event loop during
  concurrent downloads.
- Staging dir is NOT a `/media/*` zone. Downloaders put files in
  `/tmp/archive-agent/staging/<archive_id>/`; the librarian
  (phase2-06) moves/renames into the right zone. Keeping them separate
  means a partial download doesn't briefly appear in Jellyfin's scan
  path.
- Respect Archive.org's retry-after header (GUARDRAILS.md). Exponential
  backoff on 429/503. If ia-get already enforces this, don't double-up.
