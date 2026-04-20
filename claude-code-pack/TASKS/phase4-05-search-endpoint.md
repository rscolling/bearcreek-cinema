# phase4-05: Search endpoint baseline (title + similar + autocomplete)

## Goal

Stand up a usable `POST /search` that handles **title** queries (the
common case — "the third man") against the FTS5 index, plus
`POST /search/similar` (cosine-from-anchor) and
`GET /search/autocomplete` (prefix type-ahead).

Full NL / descriptive intent parsing with the small Ollama model and
the heuristic/FTS probe/LLM classifier cascade lives in phase4-08.
This card covers the endpoint surface + the FTS-only slice so the
Roku scaffolding (phase 5) can land in parallel.

## Prerequisites

- phase4-01 (scaffold)
- phase3-02 (TF-IDF index — used by `/search/similar`)
- phase3-09 (FTS5 indexing + fts_search + fts_autocomplete)

## Inputs

- `CONTRACTS.md` §3 SearchResultItem, AutocompleteSuggestion
- `state.queries.search.fts_search` / `fts_autocomplete`
- `ranking.tfidf.TFIDFIndex`

## Deliverables

1. `src/archive_agent/api/routes/search.py`:

   ```python
   @router.post("/search")
   async def search(req: SearchRequest) -> SearchResponse

   @router.post("/search/similar")
   async def search_similar(req: SimilarRequest) -> {"items": [...]}

   @router.get("/search/autocomplete")
   async def autocomplete(q: str, limit: int = 10) -> {"suggestions": [...]}
   ```

2. `SearchRequest`:
   - `query: str` (required, min_length=1)
   - `limit: int = 10` (1..50)
   - `type: Literal["movie","show","any"] = "any"`

3. Baseline `search()` behavior:
   - Always treat `query` as a title intent (TFS FTS5)
   - Result `intent = "title"`, `filter = null`
   - `match_reason` = "title match" for every result
   - Phase4-08 replaces this with the router dispatch — keep this
     function simple for now so 4-08 has a clean replacement target.

4. `SimilarRequest`:
   - `anchor_archive_id: str`
   - `limit: int = 10` (1..50)

5. `similar()` behavior:
   - Look up the anchor's row in the TF-IDF index (lazy `build(conn)`
     cached on app state — don't rebuild per-request)
   - `linear_kernel(anchor_vec, matrix)` → top-N non-self
   - Exclude `disliked_archive_ids` / `disliked_show_ids` from the
     latest profile (if a profile exists)
   - `match_reason` = "similar to <anchor title>"

6. `SearchResultItem.status` computation
   (`api/serializers.py:to_search_result_item`):
   - `ready` — `jellyfin_item_id is not None`
   - `downloadable` — `jellyfin_item_id is None` and candidate status
     in {`new`, `ranked`, `approved`}
   - `discoverable` — candidate was inserted during a live archive.org
     lookup (deferred to 4-08); for this card, no discoverable
     results.

7. Tests in `tests/unit/api/test_search.py`:
   - `/search` with a title query returns matching items and
     `intent=title`
   - `/search` with no match returns `items=[]`, still 200
   - `/search/similar` with a known anchor returns items that don't
     include the anchor itself
   - `/search/similar` with an unknown anchor returns 404
   - `/search/autocomplete?q=the` returns title suggestions
   - Status field is populated correctly based on
     `jellyfin_item_id`

## Done when

- [ ] `curl -X POST /search` with `{"query": "third man"}` returns
  the matching candidate
- [ ] `curl -X POST /search/similar` with a real anchor returns
  related items, anchor excluded
- [ ] `curl /search/autocomplete?q=th` returns suggestions
- [ ] status field is correct across ready / downloadable cases
- [ ] Unit tests pass, mypy clean

## Verification

```bash
curl -s -X POST http://localhost:8788/search \
  -H "Content-Type: application/json" \
  -d '{"query": "third man", "limit": 5}' | jq
curl -s -X POST http://localhost:8788/search/similar \
  -H "Content-Type: application/json" \
  -d '{"anchor_archive_id": "third_man_1949"}' | jq
curl -s "http://localhost:8788/search/autocomplete?q=th&limit=5" | jq
```

## Out of scope

- NL / descriptive intent parsing — phase4-08
- "More like X" convenience (descriptive with anchor) — phase4-08
- Live archive.org fallback (discoverable results) — phase4-08
- Rate limiting — not needed for the FTS-only slice

## Notes

- Cache the TF-IDF index on `app.state` — rebuilding for every
  `/search/similar` would cost 100ms+. Refresh on a schedule or
  after large discovery sweeps (daemon loop concern).
- `SearchResponse.filter` is always `null` in this card. The field
  stays in the response so 4-08 can fill it without a client-side
  breaking change.
- Trigram FTS5 does substring / prefix matching, NOT transposition
  tolerance (documented in phase3-09). "thrid man" returns nothing;
  "thir" returns the third man. That's the actual behavior; don't
  promise more in docs.
