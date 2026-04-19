# phase1-05: Ollama smoke test + LLMProvider scaffold

## Goal

Verify Ollama is reachable with `qwen2.5:7b`, round-trip a structured
Pydantic response via `instructor`, and establish the `LLMProvider`
interface skeleton (not full implementations yet).

## Prerequisites

- phase1-02 (config)
- Ollama installed on the host, `qwen2.5:7b` pulled (see `ENVIRONMENT.md`)

## Inputs

- `LLMProvider` contract from `CONTRACTS.md` section 2
- Ollama configuration from `config.toml`

## Deliverables

1. `src/archive_agent/ranking/provider.py` — `LLMProvider` Protocol:

   ```python
   from typing import Protocol

   class LLMProvider(Protocol):
       name: str                               # "ollama" | "claude" | "tfidf"

       async def health_check(self) -> HealthStatus: ...
       async def rank(self, profile, candidates, n=5) -> list[RankedCandidate]: ...
       async def update_profile(self, current, events) -> TasteProfile: ...
       async def parse_search(self, query) -> SearchFilter: ...
   ```

2. `src/archive_agent/ranking/ollama_provider.py` — `OllamaProvider`
   class:
   - Constructor takes `config.llm.ollama`
   - `health_check()` fully implemented: list models, verify
     `qwen2.5:7b` present, issue a minimal structured-output test prompt
     with a 2-field Pydantic model, confirm it round-trips
   - `rank()`, `update_profile()`, `parse_search()` raise
     `NotImplementedError` (stubs for later tasks)
   - Uses `instructor.from_provider("ollama/qwen2.5:7b",
     base_url=config.host, mode=instructor.Mode.JSON)`
   - Every call (even smoke test) logs to `llm_calls` table

3. `src/archive_agent/ranking/claude_provider.py` — `ClaudeProvider`
   class:
   - Same shape as Ollama provider
   - `health_check()` verifies API key, lists models
   - Other methods are `NotImplementedError` for now

4. `src/archive_agent/ranking/tfidf_provider.py` — `TFIDFProvider` class:
   - `health_check()` returns ok always (no external dep)
   - Other methods `NotImplementedError` for now

5. `src/archive_agent/ranking/factory.py`:

   ```python
   def make_provider(
       name: Literal["ollama", "claude", "tfidf"],
       config: Config,
   ) -> LLMProvider: ...

   def make_provider_for_workflow(
       workflow: str,
       config: Config,
   ) -> LLMProvider:
       """Reads config.llm.workflows to pick the provider."""
   ```

6. CLI integration:
   - `archive-agent health ollama` — runs OllamaProvider.health_check,
     prints model and latency
   - `archive-agent health claude` — same for Claude
   - `archive-agent health all` — runs health on Ollama, Claude (if
     configured), Jellyfin, state DB, disk; returns JSON

7. Tests:
   - `tests/integration/test_ollama_smoke.py`:
     - Gated on `RUN_INTEGRATION_TESTS=1`
     - Connects to real Ollama, round-trips a 2-field Pydantic model,
       verifies values make sense
   - `tests/unit/test_factory.py`:
     - `make_provider("ollama", ...)` returns an OllamaProvider instance
     - Invalid name raises `ValueError`
   - `tests/unit/test_llm_calls_logging.py`:
     - Provider `health_check` writes one row to `llm_calls` with correct
       fields

## Done when

- [ ] `archive-agent health ollama` succeeds with model name and latency
- [ ] Smoke test writes a row to `llm_calls` with `outcome=ok`
- [ ] `instructor` round-trip confirmed: prompt → validated Pydantic
  object back
- [ ] Provider stubs for `rank`, `update_profile`, `parse_search` raise
  `NotImplementedError` with a clear message
- [ ] `tests/integration/test_ollama_smoke.py` passes when run with
  `RUN_INTEGRATION_TESTS=1`
- [ ] Unit tests all pass
- [ ] `mypy --strict` passes

## Smoke test example

```python
from pydantic import BaseModel

class Smoke(BaseModel):
    ok: bool
    model: str

async def smoke_test(provider: OllamaProvider) -> Smoke:
    response = await provider.instructor_client.chat.completions.create(
        model="qwen2.5:7b",
        messages=[{"role": "user", "content": 'Return JSON: {"ok": true, "model": "qwen2.5"}'}],
        response_model=Smoke,
    )
    return response
```

If the returned `Smoke.ok` is True, the provider is working.

## Verification commands

```bash
archive-agent health ollama
# → {"status": "ok", "model": "qwen2.5:7b", "latency_ms": 324, "smoke_test": "passed"}

archive-agent health all
# → {
#     "ollama": {...},
#     "jellyfin": {...},
#     "state_db": {"status": "ok", "schema_version": 1},
#     "disk": {"status": "ok", "used_gb": 0.0, "budget_gb": 500}
#   }

sqlite3 $STATE_DB "SELECT provider, model, workflow, outcome, latency_ms FROM llm_calls ORDER BY id DESC LIMIT 5;"
# → shows the health check call

RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_ollama_smoke.py -v
```

## Out of scope

- Actual ranking (phase3-01)
- Profile updates (phase3-02)
- NL search parsing (phase3-03)

## Notes

- `instructor.from_provider("ollama/qwen2.5:7b", ...)` in newer
  `instructor` versions; if that syntax isn't working, fall back to
  `instructor.patch(ollama.Client(host=...), mode=instructor.Mode.JSON)`.
- Ollama's default port is 11434. Don't hardcode — always read from
  `config.llm.ollama.host`.
- The smoke test prompt should be trivial and model-agnostic. Don't
  embed knowledge in it.
- Latency is logged in the `llm_calls` row; also returned in
  `health_check` response for the CLI.
