# phase4-03: `/recommendations*` endpoints

## Goal

Wire phase 3's `recommend()` command + `latest_batch` audit log into
the HTTP surface the Roku app consumes. The daemon loop produces
batches on a schedule; the Roku asks for "the latest batch" via these
endpoints and never triggers a fresh LLM call on the request path.

## Prerequisites

- phase4-01 (scaffold)
- phase3-08 (`commands.recommend` + `ranked_candidates` table)

## Inputs

- `CONTRACTS.md` §3 (endpoint + RecommendationItem schemas)
- Existing `state.queries.ranked_candidates.latest_batch`
- Existing `commands.recommend.recommend`

## Deliverables

1. `src/archive_agent/api/routes/recommendations.py`:

   ```python
   @router.get("/recommendations")
   async def list_recommendations(
       type: Literal["movie", "show", "any"] = "any",
       limit: int = 10,
       conn: Annotated[...] = Depends(get_db),
   ) -> {"items": [RecommendationItem, ...]}

   @router.get("/recommendations/for-tonight")
   async def for_tonight(...) -> {"items": [RecommendationItem x 3]}

   @router.post("/recommendations/{archive_id}/reject")
   @router.post("/recommendations/{archive_id}/defer")
   ```

2. `RecommendationItem` builder in
   `src/archive_agent/api/serializers.py`:

   ```python
   def to_recommendation_item(
       ranked: RankedCandidate, conn: sqlite3.Connection
   ) -> RecommendationItem:
       """Join the RankedCandidate with the latest candidate row
       (for Jellyfin item id, updated genres, etc.) and fabricate the
       poster_url as `/poster/{archive_id}` so the client never talks
       to archive.org directly."""
   ```

3. `GET /recommendations`:
   - Reads `latest_batch` via `ranked_candidates`
   - Filters by type
   - Truncates to `limit`
   - Returns `[]` with a 200 when no batch exists yet (not 404 — the
     client polls after bootstrap)

4. `GET /recommendations/for-tonight`:
   - Same source, but picks 3 items weighted by time-of-day:
     - 17:00-22:00 local → prefer feature-length (runtime ≥ 90min)
     - 22:00-02:00 local → prefer short (runtime ≤ 60min)
     - Otherwise → no filter
   - Local time comes from server TZ; don't try to infer client TZ.

5. `POST /recommendations/{archive_id}/reject`:
   - Insert a `TasteEvent(kind=REJECTED, strength=0.3, ...)`
     referencing that archive_id
   - Update the candidate's status to `rejected` (terminal state per
     contract)
   - Return 204

6. `POST /recommendations/{archive_id}/defer`:
   - Insert a `TasteEvent(kind=DEFERRED, strength=0.2, ...)`
   - Leave the candidate status alone
   - Return 204

7. Tests in `tests/unit/api/test_recommendations.py`:
   - Populated DB + batch → 200 with items
   - Empty DB → 200 with `[]`
   - Type filter narrows correctly
   - For-tonight returns 3 items (or fewer if batch has fewer)
   - reject + defer both insert correct taste_events rows
   - reject marks the candidate `rejected`

## Done when

- [ ] `curl /recommendations?limit=5` returns the latest batch
- [ ] `curl -X POST /recommendations/<id>/reject` returns 204 and
  writes a taste_events row
- [ ] For-tonight time-of-day logic uses server local time
- [ ] Unit tests pass, mypy clean

## Verification

```bash
curl -s http://localhost:8788/recommendations?type=any | jq '.items | length'
curl -s -X POST http://localhost:8788/recommendations/abc123/reject -w '%{http_code}'
# expect 204
sqlite3 $STATE_DB "SELECT kind FROM taste_events WHERE kind IN ('rejected','deferred') ORDER BY id DESC LIMIT 2;"
```

## Out of scope

- Triggering a fresh `recommend()` on request — that's the daemon
  loop's job. The endpoint reads `latest_batch` only.
- `/select` (download trigger) — phase4-04.
- `/shows/{id}/commit` (sampler bypass) — phase4-04.
- Poster proxying — phase4-06; `poster_url` is a string here.

## Notes

- RankedCandidate's `reasoning` flows straight through to the Roku
  detail screen. Don't truncate or reformat it in the serializer.
- For-tonight's time-window logic is deliberately server-side: a Roku
  can't be trusted to pass an accurate timezone, and the household
  physics of "it's late, something short" doesn't actually vary by
  time zone on a LAN.
- reject/defer write taste events but don't re-run ranking. The next
  scheduled `run_if_due` picks them up on the normal cadence.
