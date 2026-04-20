# phase3-07: ClaudeProvider full implementation

## Goal

Fill in `ClaudeProvider` so it's a drop-in replacement for
`OllamaProvider` at the `LLMProvider` Protocol level. Users who
opt in via `config.llm.claude.enabled=true` (per workflow or
globally) get Claude-quality recommendations and profile summaries
at the cost of real money per call.

This is strictly opt-in (ADR-001). The daemon never silently routes
to Claude — if the user configured Ollama and Ollama breaks, the
fallback is TFIDF, not Claude (ADR-002).

## Prerequisites

- phase1-05 (ClaudeProvider stub + Protocol)
- phase3-03 (OllamaProvider `rank` — prompts are shared)
- phase3-05 (profile-update prompt — shared)

## Inputs

- ADR-001 (Ollama default, Claude opt-in)
- ADR-010 (`instructor` for unified structured output)
- `CONTRACTS.md` §2 LLMProvider, §5 config
- `config.llm.claude.{api_key, model, timeout_s, max_tokens}`
- `ANTHROPIC_API_KEY` env var (redacted in logs — phase1-06)
- Model: default `claude-sonnet-4-6` (per system prompt: "When
  building AI applications, default to the latest and most capable
  Claude models"). Allow override via config.
- Prompt templates from phase3-03 and phase3-05 — same Jinja
  files, re-rendered. No Claude-specific prompt engineering in v1.

## Deliverables

1. Fill in `src/archive_agent/ranking/claude_provider.py`:

   ```python
   class ClaudeProvider:
       def __init__(
           self,
           api_key: str,
           model: str = "claude-sonnet-4-6",
           timeout_s: float = 60.0,
           max_tokens: int = 4096,
       ) -> None: ...

       async def health_check(self) -> HealthStatus:
           """Ping with a 5-token completion. 'ok' on success, 'down'
           on auth failure, 'degraded' on timeout."""

       async def rank(
           self, profile, candidates, n=5, *, ratings=None
       ) -> list[RankedCandidate]:
           """Same prompt as OllamaProvider.rank. Uses
           instructor.from_anthropic(client, mode=Mode.TOOLS) with
           the _RankResponse pydantic model. Never raises out — falls
           back to trivial ranking on total failure, identical
           semantics to OllamaProvider."""

       async def update_profile(self, current, events) -> TasteProfile:
           """Shared prompt from phase3-05. Response model is
           TasteProfile directly."""

       async def parse_search(self, query: str) -> SearchFilter:
           """Structured output to SearchFilter. Deterministic at
           temperature=0."""
   ```

2. `llm_calls` persistence must record:
   - `provider = "claude"`, `model = <actual_model>`
   - token counts from the Anthropic response (`input_tokens`,
     `output_tokens`)
   - cost estimate in cents using hardcoded per-model rates table
     (a tiny helper `_estimate_cost_cents(model, input, output)`)
   - full latency in ms

3. Per-workflow selection in the factory:

   ```python
   def provider_for_workflow(
       config: Config, workflow: Literal["rank", "profile", "search"]
   ) -> LLMProvider:
       """Reads config.llm.claude.workflows — a list of workflow
       names that should route to Claude. All others route to
       Ollama. Default list: []. Unknown workflow names raise."""
   ```

   Config surface (extends `CONTRACTS.md` §5):

   ```toml
   [llm.claude]
   enabled = false
   api_key = "${ANTHROPIC_API_KEY}"
   model = "claude-sonnet-4-6"
   workflows = ["rank"]           # opt in per workflow
   ```

4. Secret handling: the redactor (phase1-06) must not emit the
   API key. Add tests that exercise a logged error path and assert
   the key doesn't appear in the structlog output.

5. CLI:
   - `archive-agent rank claude --n 5` — force a Claude rank call
     regardless of config (for testing). Refuses politely with
     "Claude is not configured" if `api_key` is missing.
   - `archive-agent llm cost [--since 2026-04-01]` — print total
     Claude spend from `llm_calls` rows in the window

6. Tests in `tests/unit/ranking/test_claude_provider.py`:
   - Mock the Anthropic SDK; assert `instructor.from_anthropic` is
     called with the right model + mode
   - Malformed response → retry → success path
   - Total failure path returns trivial ranking, never raises
   - `llm_calls` row includes provider="claude" + non-zero token
     counts
   - API key redaction: log-capture test asserts no secret leaks

7. Integration test (opt-in via env + non-zero cost warning):
   - `RUN_CLAUDE_INTEGRATION=1` + `ANTHROPIC_API_KEY` set → run a
     real rank call, assert shape + at least one `llm_calls` row
   - Prints "This test costs ~$0.01" at start so nobody runs it by
     accident in CI

## Done when

- [ ] `archive-agent rank claude --n 5` returns a shaped response
  against a live API key
- [ ] Per-workflow routing actually routes (`rank` to Claude,
  `profile` to Ollama when configured that way)
- [ ] No silent fallback to Claude (ADR-001) — only explicit config
  enables it
- [ ] API key never appears in logs
- [ ] Cost estimate lands in `llm_calls` rows
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
export ANTHROPIC_API_KEY=sk-ant-...
archive-agent rank claude --n 5
archive-agent llm cost
pytest tests/unit/ranking/test_claude_provider.py -v
RUN_CLAUDE_INTEGRATION=1 pytest tests/integration/test_claude_provider.py
```

## Out of scope

- Caching Claude responses — costs are small enough that it's not
  needed for v1
- Fine-grained rate limiting — the Anthropic SDK already handles it
- Model selection UI — config-driven only
- Migration tooling for old logs that lack `provider` column — not
  an issue, column exists from phase1-06

## Notes

- Default model is Sonnet 4.6. Opus is overkill for ranking. Users
  who want Opus can set it in config; don't encourage it.
- The `max_tokens=4096` default is enough for 10 picks with long
  reasoning and a full profile summary. Don't default higher — it
  costs more and doesn't help output quality.
- Cost estimation rates are going to drift. Put them in a single
  dict `_CLAUDE_COSTS` at the top of the file with a comment:
  "Verify against anthropic.com/pricing when rates change."
- `instructor.from_anthropic(client, mode=Mode.TOOLS)` is the
  recommended idiom. Don't use `Mode.JSON` — Claude's tool-use API
  is more reliable for structured output.
- The API key env-var pattern is the same as TMDb in phase2-02.
  Match the interpolation style in `config.py`.
