# phase4-08: Query router and search endpoint

## Goal

Build the query router that classifies user input into title / descriptive
/ play-command / unknown, and wires up `POST /search`,
`POST /search/similar`, and `GET /search/autocomplete` endpoints.

## Prerequisites

- phase3-09 (FTS5 indexing)
- phase3-02 (TF-IDF prefilter, for similarity)
- phase3-03 (Ollama rank, for descriptive intent ranking)
- phase4-01 (FastAPI scaffold)

## Inputs

- `docs/search-and-retrieval.md` §"The query router" and §"API surface"

## Deliverables

1. `src/archive_agent/search/router.py`:

   ```python
   class QueryIntent(str, Enum):
       TITLE = "title"
       DESCRIPTIVE = "descriptive"
       PLAY_COMMAND = "play"
       UNKNOWN = "unknown"

   class QueryRouteResult(BaseModel):
       intent: QueryIntent
       normalized_query: str
       filter: SearchFilter | None = None  # set for descriptive
       anchor_archive_id: str | None = None  # set for "more like X"

   async def route_query(
       query: str,
       llm: LLMProvider,
       fts_probe: FtsProbeFn,
   ) -> QueryRouteResult:
       """Three-stage strategy:
       1. Regex heuristics (play/watch prefixes, "more like X" pattern)
       2. FTS probe — if query strongly matches a title, intent=TITLE
       3. LLM classification fallback for genuinely ambiguous input"""
   ```

2. Heuristic layer:
   - `^(play|watch)\s+(.+)$` → PLAY_COMMAND, strip verb
   - `^(more\s+like|similar\s+to)\s+(.+)$` → DESCRIPTIVE with anchor
   - 1-4 words all alphanumeric and no descriptive adjectives → try TITLE first
   - Contains any of a curated descriptive-term list → DESCRIPTIVE

3. Curated descriptive-term list in
   `src/archive_agent/search/descriptive_terms.py`:
   - Genre words: noir, comedy, western, sitcom, mystery, horror, etc.
   - Quality/length: short, long, quick, classic, modern
   - Mood: funny, scary, heartwarming, dark
   - Era: forties, fifties, pre-code, silent
   - Negations: "anything but", "not", "except"

4. Query normalization:
   - Lowercase
   - Collapse whitespace
   - Expand common ASR oddities: "3rd" → "third", "2" → "two" (but not
     in years — don't rewrite "1949" as "one thousand nine hundred...")
   - Strip leading/trailing punctuation

5. LLM classifier (`llama3.2:3b`):

   ```python
   class RoutingDecision(BaseModel):
       intent: Literal["title", "descriptive", "play", "unknown"]
       confidence: float  # 0.0-1.0
       extracted_title: str | None  # for play command
       reasoning: str  # 1 sentence

   async def llm_classify(query: str, provider: LLMProvider) -> RoutingDecision
   ```

   Uses structured output via instructor. Only called when heuristics
   and FTS probe don't converge.

6. Endpoints in `src/archive_agent/api/routes/search.py`:

   ```python
   @router.post("/search")
   async def search(req: SearchRequest) -> SearchResponse: ...

   @router.post("/search/similar")
   async def search_similar(req: SimilarRequest) -> SearchResponse: ...

   @router.get("/search/autocomplete")
   async def autocomplete(q: str, limit: int = 10) -> AutocompleteResponse:
       ...
   ```

   `search()` orchestrates: route → dispatch (FTS for title / intent parser
   + TF-IDF + ranker for descriptive / etc.) → SearchResultItem list.

7. SearchResultItem status determination:
   - Joins candidate status with Jellyfin linkage to return the right
     `status` value: "ready" | "downloadable" | "discoverable"
   - Populates `next_episode` for shows from the show_state table
   - `match_reason` is short, generated from either FTS match info
     ("title match") or LLM reasoning (for descriptive)

8. Live Archive.org fallback for search miss:
   - Only if FTS returns no results with bm25 below minimum threshold
   - Rate-limited: max 1 call per user per 10 seconds
   - Results are inserted into `candidates` table with status=NEW before
     returning, so subsequent searches hit the cache

9. Tests:
   - `tests/unit/search/test_router.py` — 30+ labeled query fixtures
     covering each intent; verify correct classification
   - `tests/unit/search/test_normalization.py`
   - `tests/integration/test_search_endpoint.py` — full round-trip
     against a populated SQLite and mocked LLM

## Done when

- [ ] All 30 golden-path queries route to correct intent
- [ ] `curl POST /search` with a title query returns correct match
- [ ] `curl POST /search` with a descriptive query returns a filtered,
  taste-ranked list
- [ ] `curl POST /search/similar` with an anchor ID returns similar
  items
- [ ] `curl GET /search/autocomplete?q=th` returns type-ahead suggestions
- [ ] Live Archive.org fallback works (integration test)
- [ ] Rate limiting on live fallback is enforced
- [ ] Mypy, ruff, tests pass

## Verification

```bash
# Start API
archive-agent serve &

# Title
curl -s -X POST http://localhost:8787/search \
  -H "Content-Type: application/json" \
  -d '{"query": "the third man"}' | jq

# Descriptive
curl -s -X POST http://localhost:8787/search \
  -H "Content-Type: application/json" \
  -d '{"query": "something noir and short"}' | jq

# More like
curl -s -X POST http://localhost:8787/search \
  -H "Content-Type: application/json" \
  -d '{"query": "more like the third man"}' | jq

# Autocomplete
curl -s "http://localhost:8787/search/autocomplete?q=th" | jq

pytest tests/unit/search/ tests/integration/test_search_endpoint.py -v
```

## Notes

- The router's output is what the API endpoint dispatches on. Keep the
  router itself dumb: classify and return, don't execute.
- The LLM fallback is expensive (~500ms cold on CPU). Heuristics and FTS
  probe should catch >80% of queries without invoking it.
- "More like X" is a two-step: route identifies DESCRIPTIVE with anchor,
  API endpoint does FTS to resolve X, then calls `/search/similar`
  pipeline. Don't try to short-circuit this into one step.
- Pay attention to match_reason prose. This shows up in the Roku UI
  and is a small quality-of-life thing that reads well or reads weird.
