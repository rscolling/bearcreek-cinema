# phase4-06: Poster proxy endpoint

## Goal

Every `poster_url` emitted by the API is `/poster/{archive_id}`. This
card implements the endpoint that proxies (and caches) the actual
image so the Roku never talks to archive.org / TMDb directly.

Why proxy: CORS is irrelevant on Roku but rate limits aren't. Caching
on don-quixote lets the Roku type-ahead preload posters cheaply, and
gives us a stable URL that doesn't break when a candidate's upstream
`poster_url` changes.

## Prerequisites

- phase4-01 (scaffold)
- phase2-02 (TMDb + existing candidate.poster_url)

## Inputs

- `CONTRACTS.md` §3 `/poster/{archive_id}` → image/jpeg
- `Candidate.poster_url`

## Deliverables

1. `src/archive_agent/api/routes/poster.py`:

   ```python
   @router.get("/poster/{archive_id}")
   async def get_poster(
       archive_id: str,
       conn: Annotated[sqlite3.Connection, Depends(get_db)],
   ) -> Response:
       """Fetch + cache + return image bytes. 404 when no
       poster_url or no candidate; 502 when upstream fails."""
   ```

2. On-disk cache: files live under
   `cfg.paths.state_db.parent / "poster_cache" / {archive_id}.{ext}`.
   - Key is `archive_id`; extension chosen from the upstream
     `Content-Type` (jpg, png, webp).
   - Cache hit: stream from disk, serve with `Cache-Control:
     public, max-age=86400`.
   - Cache miss: `httpx.AsyncClient().get(poster_url)` with 10s
     timeout; on success, write to a tmp file + rename into place.

3. `cache_size_limit_mb` added to `ApiConfig`
   (default 200). When the cache directory exceeds this, evict the
   oldest-accessed files until under. Safety net runs on each miss,
   not on every request.

4. Error handling:
   - No candidate → 404
   - Candidate has no `poster_url` → 404
   - Upstream timeout → 502 with retry-after header
   - Upstream 4xx/5xx → 502 (we're always the intermediary; don't
     proxy upstream codes blindly)

5. Tests in `tests/unit/api/test_poster.py`:
   - Hit-then-miss: first call writes cache, second call reads from
     disk without upstream call (assert on mocked httpx call count)
   - Unknown archive_id → 404
   - Candidate with poster_url=None → 404
   - Upstream error → 502
   - Cache eviction when directory exceeds limit

## Done when

- [ ] `curl -o poster.jpg /poster/<archive_id>` returns an image and
  writes the cache file
- [ ] Second call served from cache (no upstream request)
- [ ] Unknown / null-poster returns 404
- [ ] Cache stays under configured limit via oldest-access eviction
- [ ] Unit tests pass, mypy clean

## Verification

```bash
curl -I http://localhost:8788/poster/$ARCHIVE_ID
# expect 200 image/jpeg
curl -I http://localhost:8788/poster/not_a_real_id
# expect 404
ls -la $STATE_DIR/poster_cache/ | head
```

## Out of scope

- Resizing — Roku can scale the 600px source fine. Don't add Pillow.
- Auth — LAN-only.
- ETag / If-Modified-Since — `Cache-Control: max-age` is enough for
  a LAN appliance.

## Notes

- Use `atime` for eviction on Linux (Docker host); fall back to
  `mtime` on Windows where `atime` often isn't tracked. The dev
  workstation's cache doesn't matter; prod is don-quixote Linux.
- Write-then-rename prevents partial files from being served on
  concurrent misses. Use `os.replace`.
- Stream the response: don't load the whole image into memory when
  cold-proxying. Use `StreamingResponse` with the httpx response's
  byte iterator.
- The upstream `poster_url` may be a TMDb CDN URL. That's fine; it
  serves fast and doesn't rate-limit household-scale traffic.
