# phase4-04: `/select` + `/shows/{id}/commit`

## Goal

Wire the "user picked this on the Roku" flow end-to-end.
`POST /recommendations/{archive_id}/select` triggers the download
pipeline, the librarian places the file, Jellyfin scans, and the
response carries the `jellyfin_item_id` the Roku needs to deep-link
into the Jellyfin app (ADR-006).

`POST /shows/{show_id}/commit` bypasses the sampler-first flow
(ADR-004) and queues a full show download — used when the Roku user
long-presses "give me the whole thing" on a sampled show.

## Prerequisites

- phase4-01 (scaffold)
- phase2-04/06/09 (downloader + placement + Jellyfin scan)
- phase2-08 (tv sampler)
- phase3-08 (ranked_candidates)

## Inputs

- `CONTRACTS.md` §3 select / commit shape
- Existing `archive.downloader.download_one`, `librarian.place`,
  `jellyfin.scan_and_resolve`

## Deliverables

1. `src/archive_agent/commands/select.py`:

   ```python
   class SelectResult(BaseModel):
       jellyfin_item_id: str | None
       play_start_ticks: int = 0
       next_episode: EpisodeInfo | None = None
       status: Literal["ready", "queued", "failed"]
       detail: str = ""

   async def select_candidate(
       conn, config, archive_id: str, *, play: bool = True
   ) -> SelectResult:
       """Orchestrate: candidate lookup → download (if not already
       placed) → placement → Jellyfin scan → resolve item id.

       Idempotent: if the candidate already has a
       `jellyfin_item_id` populated, returns ready immediately.
       """
   ```

2. Movie flow:
   - If `jellyfin_item_id` set and file on disk → return ready.
   - Else: `download_one` → `librarian.place(zone=recommendations)`
     → `scan_and_resolve` → update candidate → return ready.
   - `play_start_ticks` stays 0 for movies unless the state DB has
     a resume mark (future-proofed; 0 for now).

3. Show flow (phase4-04 scope: sampler-start, not full commit):
   - Trigger `librarian.tv_sampler.step_show` under the hood. The
     sampler decision (start_sampling / wait / promote) dictates
     what happens, but from the Roku's perspective:
     - If any sampler episode has `jellyfin_item_id` → return ready
       with the **first sampler episode** in `next_episode`
     - Else → return queued with detail="sampler_started"

4. `POST /recommendations/{archive_id}/select`:
   - Body: `{"play": bool}` (default true; honored for future
     `/select?play=false` book-and-don't-autoplay UI)
   - Response: `SelectResult`
   - 200 for ready, 202 for queued, 500+problem+json for failed

5. `POST /shows/{show_id}/commit`:

   ```python
   class CommitResult(BaseModel):
       enqueued_downloads: int
       estimated_gb: float
   ```

   - Bypasses `tv_sampler.step_show` — enqueues every episode
     candidate for the show directly into the download queue with
     zone=tv (promoted placement).
   - Returns 202 immediately; downloads happen async.

6. Tests in `tests/unit/commands/test_select.py` and
   `tests/unit/api/test_select.py`:
   - Movie already placed → ready path, no downloader call
   - Movie not placed → downloader mocked, placement called,
     scan_and_resolve returns a fake item id
   - Movie download fails → SelectResult.status=failed, 500 in API
   - Show sampler flow → queued with sampler_started
   - `commit` enqueues N episodes (fake downloader records count)

## Done when

- [ ] `curl -X POST /recommendations/<movie_id>/select` returns
  ready + a jellyfin_item_id when the file is already in place
- [ ] Fresh candidate select triggers download + placement + scan
- [ ] `curl -X POST /shows/<show_id>/commit` returns 202 with a
  plausible GB estimate
- [ ] Idempotent — second select of the same archive_id is fast and
  doesn't re-download
- [ ] Unit tests pass, mypy clean

## Verification

```bash
# Pick a candidate already present
curl -s -X POST http://localhost:8788/recommendations/$MOVIE/select \
  -H "Content-Type: application/json" -d '{"play": true}' | jq

# Bypass sampler for a show
curl -s -X POST http://localhost:8788/shows/$SHOW/commit | jq
```

## Out of scope

- Actual Roku deep-link construction — the Roku app owns that,
  given `jellyfin_item_id`.
- Parental controls / per-user playback gating — single-household
  (ADR-007).
- Retry-on-failure — failed downloads stay failed until the next
  daemon pass retries.

## Notes

- `select` is the one endpoint that does non-trivial work on the
  request path. Keep its timeout generous (60s+) on the client and
  return 202 quickly when the work is truly async (sampler start).
- Respect ADR-006: the response does NOT include a playback URL.
  Only `jellyfin_item_id`. The Roku client constructs the ECP
  deep-link.
- If `play=false` is passed, still download + place + scan, but the
  Roku UI won't auto-kick playback. This supports "bookmark this,
  I'll watch later" without extra API surface.
