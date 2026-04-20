# phase3-03: Ollama reranker

## Goal

Fill in the stub `OllamaProvider.rank()` from phase1-05 with the real
implementation: take a TasteProfile + a shortlist of candidates (from
phase3-02 prefilter), ask `qwen2.5:7b` to pick the best n with one-line
reasoning each, return `RankedCandidate`s ordered by rank.

This is stage 2 of the two-stage pipeline. Output quality of this
function is the single biggest driver of recommendation quality in the
whole system.

## Prerequisites

- phase1-05 (Ollama provider skeleton — stubs exist)
- phase1-06 (logging + `llm_calls` persistence — every call must land a row)
- phase3-02 (prefilter produces the shortlist this reads)

## Inputs

- `CONTRACTS.md` §2 (LLMProvider interface, behavioral guarantees)
- ADR-010 (`instructor` for unified structured output)
- ADR-013 (ratings are priors the prompt must mention)
- `config.llm.ollama.{model, timeout_s, num_ctx}`
- `archive_agent.testing.token_budget.check_prompt_fits` — the prompt
  must be verified to fit in `num_ctx` with >=20% margin

## Deliverables

1. Fill in `src/archive_agent/ranking/ollama_provider.py`:

   ```python
   async def rank(
       self,
       profile: TasteProfile,
       candidates: list[Candidate],
       n: int = 5,
   ) -> list[RankedCandidate]:
       # 1. Build prompt (see prompts/rank.j2)
       # 2. Call instructor.from_provider("ollama/qwen2.5:7b", ...)
       #    with response_model=_RankResponse (list[_RankItem])
       # 3. Retry up to config.llm.retries times on validation failure
       # 4. Emit llm_calls row (success or failure)
       # 5. On total failure, fall back to trivial ranking: return
       #    candidates[:n] ordered by prefilter score (caller supplies
       #    via candidates order — it's already sorted descending)
       #    with reasoning "Fallback: similarity match."
   ```

2. Prompt template `src/archive_agent/ranking/prompts/rank.j2`:
   - Include profile.summary (prose — the LLM's best context)
   - Include profile.liked_genres, .disliked_genres, .era_preferences
   - For each candidate: title, year, content_type, genres, runtime,
     1-line description slice
   - **Inject per-show rating priors (ADR-013):** for any candidate
     whose `show_id` has a latest rating in the last 180 days, append
     ` [rated: 👎|👍|👍👍]` to its listing so the LLM treats it as a
     strong prior. "more like this" for LOVE, "similar to this → downrank"
     for DOWN. Pass ratings via a new `ratings: dict[str, TasteEvent]`
     kwarg (keyed by show_id) that `rank()` accepts in addition to the
     contract signature. Plumb from phase3-08's recommend command.
   - Ask for exactly `n` picks as a JSON list: `[{"archive_id", "score"
     (0..1), "reasoning" (<= 150 chars)}, ...]`
   - Instruct the model to explain each pick in concrete terms ("pairs
     the screwball pacing you liked in *His Girl Friday* with a
     wartime ensemble" — not "you'll enjoy this one")

3. Pydantic response models (internal, for instructor):

   ```python
   class _RankItem(BaseModel):
       archive_id: str
       score: float = Field(ge=0.0, le=1.0)
       reasoning: str = Field(min_length=20, max_length=200)

   class _RankResponse(BaseModel):
       picks: list[_RankItem]
   ```

4. Signature extension (non-breaking — adds a kwarg with a default):

   ```python
   async def rank(
       self,
       profile: TasteProfile,
       candidates: list[Candidate],
       n: int = 5,
       *,
       ratings: dict[str, TasteEvent] | None = None,
   ) -> list[RankedCandidate]: ...
   ```

   Update the `LLMProvider` Protocol in `provider.py` and the other
   two implementations (TF-IDF + Claude) to accept the kwarg (TF-IDF
   ignores it; Claude will use it in phase3-07).

5. Prompt fits in context: call
   `check_prompt_fits(prompt, model=config.llm.ollama.model,
   num_ctx=config.llm.ollama.num_ctx, margin_pct=0.2)` before sending.
   Raise at module import time (in a smoke test, not runtime) if a
   50-candidate prompt blows the budget.

6. Tests in `tests/unit/ranking/test_ollama_rank.py`:
   - Uses `instructor` mock or `respx` against the Ollama HTTP API
   - Happy path: 50 candidates in, 5 RankedCandidates out, ordered,
     reasoning non-empty, scores in [0, 1]
   - Validation failure: first response is malformed → retries → second
     succeeds
   - Total failure (all retries malformed): returns trivial ranking
     from the first `n` candidates, never raises
   - Rating injection: passing `ratings={show_id: RATED_LOVE}` puts
     "👍👍" into the prompt text for that candidate (assert on the
     rendered string)
   - `llm_calls` row is written on both success and total-failure paths

7. Integration test `tests/integration/test_ollama_rank.py` (skipped
   unless `RUN_INTEGRATION_TESTS=1`):
   - Real Ollama, real profile+candidates fixtures
   - Assert shape only (ordered list of 5, reasoning non-empty) — not
     specific content, which is non-deterministic

## Done when

- [ ] `archive-agent rank ollama --n 5` (new CLI subcommand) returns
  5 picks with reasoning against the current profile + fresh
  prefilter shortlist
- [ ] Malformed LLM output never raises out of `rank()`
- [ ] `llm_calls` row written for every call attempt
- [ ] Rating priors from ADR-013 flow into the prompt text
- [ ] Prompt fits model context with 20% margin at 50 candidates
- [ ] `mypy --strict` passes
- [ ] Unit tests pass; integration test passes when opted in

## Verification commands

```bash
archive-agent rank prefilter --k 50 > /tmp/shortlist.json
archive-agent rank ollama --n 5
sqlite3 $STATE_DB "SELECT provider, model, latency_ms, ok FROM llm_calls ORDER BY id DESC LIMIT 5;"
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_ollama_rank.py -v
```

## Out of scope

- ClaudeProvider wiring — phase3-07
- TFIDFProvider `rank()` full implementation — phase3-06
- `update_profile` / `parse_search` on OllamaProvider — phase3-05 /
  phase4-05 / phase4-08

## Notes

- Reasoning character limit (200) is a hard cap. Overly long reasoning
  wastes tokens and often pads with fluff. The prompt should ask for
  one crisp sentence.
- Score ordering: the LLM's `score` is its confidence, not the final
  rank. `rank` in the returned `RankedCandidate` comes from sorting
  by `score` descending then renumbering 1..n. Don't trust the LLM to
  hand you monotonic scores.
- If the LLM returns >n picks, truncate. <n picks → retry once; if
  still short, pad from prefilter tail with fallback reasoning.
- Disliked genres + disliked IDs are already filtered by prefilter,
  so the prompt's job is discrimination *within* a plausible pool,
  not exclusion.
- Rating priors window: 180 days. An old 👎 shouldn't lock you out
  of a show forever — tastes change.
