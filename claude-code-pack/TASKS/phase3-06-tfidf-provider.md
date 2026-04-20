# phase3-06: TFIDFProvider full implementation

## Goal

Flesh out `TFIDFProvider` so it satisfies the full `LLMProvider`
Protocol — `rank`, `update_profile`, `parse_search`, `health_check` —
using only the in-memory TF-IDF index and deterministic logic. No
LLM calls.

This is the **fallback of last resort** per ADR-002: when Ollama is
down or returning garbage, the system still produces recommendations
instead of erroring. TFIDFProvider is also what the daemon uses
during the cold-start window before the first Ollama health check
completes.

## Prerequisites

- phase3-02 (TFIDFIndex + prefilter)
- phase1-05 (provider skeleton + stub TFIDFProvider class)

## Inputs

- ADR-002 (TF-IDF never fails; fallback order is Ollama → TF-IDF,
  **never** silently to Claude)
- `CONTRACTS.md` §2 LLMProvider behavioral guarantees
- Existing `src/archive_agent/ranking/tfidf_provider.py` stub

## Deliverables

1. Fill in `src/archive_agent/ranking/tfidf_provider.py`:

   ```python
   class TFIDFProvider:
       def __init__(self, index: TFIDFIndex) -> None:
           self._index = index

       async def health_check(self) -> HealthStatus:
           """Always 'ok' as long as the index has rows. 'degraded'
           if index is empty (no candidates to rank)."""

       async def rank(
           self,
           profile: TasteProfile,
           candidates: list[Candidate],
           n: int = 5,
           *,
           ratings: dict[str, TasteEvent] | None = None,
       ) -> list[RankedCandidate]:
           """Vectorize profile into a query, cosine-score each
           candidate, return top-n. Reasoning is templated:
             "Similar to: {top 2 liked titles in same genre}"
           Ratings (ADR-013) adjust scores:
             RATED_LOVE: +0.3 bump for candidates sharing a show_id,
             RATED_DOWN: -0.5 penalty; apply before top-n sort."""

       async def update_profile(
           self,
           current: TasteProfile,
           events: list[TasteEvent],
       ) -> TasteProfile:
           """Deterministic merge, no LLM:
           - version = current.version + 1
           - updated_at = now
           - liked_genres / disliked_genres: merge by tallying event
             kinds → genres (requires a candidate→genre join)
           - liked_archive_ids / liked_show_ids: union positive kinds
           - disliked_*: union negative kinds; also remove from liked
           - era_preferences: recompute from finished movies' decades
           - runtime_tolerance_minutes: 95th pctile of finished runtimes
           - summary: templated prose, ~150 words:
             "Household watches a mix of {top 3 liked genres}.
              Particular favorites include {3 titles}. Tends away from
              {top 2 disliked genres} and {brief era note}."
           """

       async def parse_search(self, query: str) -> SearchFilter:
           """Keyword extraction. No NL — just:
           - Split on whitespace, drop stopwords
           - Keywords: remaining tokens
           - content_types: if 'movie'/'film' in query → MOVIE;
             'show'/'series'/'tv' → SHOW
           - era: detect '40s', '1940s', '1940-1959' patterns
           - max_runtime_minutes: detect 'short', 'feature' (<=120)
           - genres: leave unset (LLM's job)
           """
   ```

2. Registration in the provider factory
   (`src/archive_agent/ranking/factory.py`):

   ```python
   async def build_fallback_chain(
       conn: sqlite3.Connection,
       config: Config,
   ) -> list[LLMProvider]:
       """Returns [primary, tfidf_fallback]. Primary is Ollama or
       Claude per config. TFIDF is always appended last. Never
       returns a chain without TFIDF at the end."""

   class FallbackProvider:
       """Composite that implements LLMProvider by delegating to
       a chain. For each method: try providers in order, catch
       exceptions, log the fallback, return the first success."""
   ```

3. Telemetry: every fallback hop emits a structlog event with
   `event="provider_fallback"`, `from=ollama`, `to=tfidf`,
   `reason=...`. This is the signal a human uses to notice that
   Ollama is dropping calls.

4. Tests in `tests/unit/ranking/test_tfidf_provider.py`:
   - `rank` returns exactly n RankedCandidates, reasoning non-empty
   - Rating boost: two nearly-identical candidates, one shares a
     show_id with a RATED_LOVE event — it ranks higher
   - Rating penalty: same setup with RATED_DOWN — it ranks lower or
     is excluded from top-n
   - `update_profile` increments version, preserves IDs, writes a
     non-empty templated summary
   - `parse_search` handles: "40s noir" → era=(1940,1949) +
     keywords=['noir']; "short documentary" →
     max_runtime_minutes=120 (or similar), keywords=['documentary']
   - `FallbackProvider` tests: primary raises → secondary called →
     result returned; primary returns empty list → secondary *not*
     called (empty is a valid response); fallback event is logged

## Done when

- [ ] `archive-agent rank tfidf --n 5` (new subcommand) produces 5
  picks without invoking any LLM
- [ ] With Ollama stopped (`docker compose stop ollama`), the full
  recommend flow still produces output — via FallbackProvider
- [ ] `FallbackProvider` wraps all three methods, not just `rank`
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
archive-agent rank tfidf --n 5

# Simulate Ollama down
docker compose -f infra/ollama/docker-compose.yaml stop
archive-agent recommend --n 5   # should still work
docker compose -f infra/ollama/docker-compose.yaml start

pytest tests/unit/ranking/test_tfidf_provider.py -v
```

## Out of scope

- Learning any weights — this is intentionally deterministic so
  it's debuggable when the LLM isn't available
- Building the TFIDFIndex — that's phase3-02
- ClaudeProvider — phase3-07

## Notes

- Templated reasoning is ugly but honest. Don't try to make the
  TF-IDF provider sound like the LLM — users who see templated
  reasoning should recognize the system is in fallback mode.
- The rating score adjustment constants (+0.3, -0.5) are
  intentionally coarser than the LLM's handling. TF-IDF can't
  reason about subtlety, so it uses hammers. Tune later only if
  the fallback path actually gets used.
- `parse_search` for TF-IDF is brittle on purpose. The real NL
  parsing lives in phase4-05 / phase4-08 using a small Ollama
  model. This exists so `LLMProvider.parse_search` never raises
  when called during a fallback.
- `FallbackProvider` must NOT silently fall through to Claude —
  only to TFIDF. If Claude is configured as the primary and it
  fails, fallback is still to TFIDF, not to Ollama (per ADR-002,
  no cross-LLM fallback).
