# phase1-06: Logging + observability

## Goal

Structured logging with `structlog`, automatic secret redaction, and the
`llm_calls` audit table wired so every LLM interaction is persisted.

## Prerequisites

- phase1-02 (config), phase1-03 (state), phase1-05 (LLMProvider stubs)

## Deliverables

1. `src/archive_agent/logging.py`:

   ```python
   def configure_logging(
       level: str = "INFO",
       format: Literal["json", "console"] = "json",
   ) -> None:
       """Set up structlog with redaction processor."""

   def get_logger(name: str) -> structlog.stdlib.BoundLogger: ...

   REDACT_FIELDS = {"api_key", "token", "password", "secret", "authorization"}

   def redact_processor(logger, method_name, event_dict):
       """Replaces values of sensitive keys with '***'."""
   ```

2. Every module imports `logger = get_logger(__name__)` instead of
   `logging.getLogger`. Replace any existing `print()` in library code
   with `logger.info(...)` / `logger.error(...)`.

3. LLM call persistence middleware:

   `src/archive_agent/ranking/audit.py`:

   ```python
   @asynccontextmanager
   async def audit_llm_call(
       provider: str,
       model: str,
       workflow: str,
   ) -> AsyncIterator[LLMCallContext]:
       """Context manager that times the call, records outcome, and writes
       to llm_calls on exit.

       Usage:
           async with audit_llm_call("ollama", "qwen2.5:7b", "rank") as ctx:
               response = await do_the_call()
               ctx.input_tokens = response.usage.input_tokens
               ctx.output_tokens = response.usage.output_tokens
               ctx.outcome = "ok"
       """
   ```

4. Wrap every method in `OllamaProvider` and `ClaudeProvider` with
   `audit_llm_call(...)`. `TFIDFProvider` uses the same wrapper with
   `provider="tfidf"`, `model="tfidf-v1"` — it's useful for comparing
   fallback usage against LLM usage.

5. CLI integration:
   - `archive-agent logs tail` — pretty-prints recent journald entries
     for the agent (wraps `journalctl`)
   - `archive-agent llm-calls stats` — queries `llm_calls`, prints:
     - Total calls by provider
     - p50/p95/p99 latency by (provider, workflow)
     - Outcome breakdown (ok, malformed, timeout, error, fallback)
     - Last 10 calls tabular

6. Tests:
   - `tests/unit/test_logging.py` — redaction processor replaces secrets
   - `tests/unit/test_audit.py` — context manager writes row on success,
     on exception, on timeout

## Done when

- [ ] All library code uses `structlog`, no `print`
- [ ] `archive-agent health ollama` call produces a readable JSON log
  line with `event=llm_health_check` and subsystem fields
- [ ] Any log line with `api_key=...` in its fields shows `***` not the
  key
- [ ] `archive-agent llm-calls stats` prints a readable report
- [ ] Every LLM call through a provider writes exactly one
  `llm_calls` row
- [ ] Exceptions during LLM calls still produce an audit row with
  `outcome=error`
- [ ] Tests pass, `mypy --strict` passes

## Verification commands

```bash
# Generate some LLM traffic
archive-agent health ollama
archive-agent health ollama
archive-agent health ollama

archive-agent llm-calls stats
# → Total: 3 | ok: 3 | latency p50/p95: 234ms/412ms

# Confirm redaction
ARCHIVE_AGENT_LOG_LEVEL=DEBUG archive-agent config show 2>&1 | grep -i "api_key"
# → should show '***' not the actual key

pytest tests/unit/test_logging.py tests/unit/test_audit.py -v
```

## Out of scope

- Metrics export (Prometheus etc.) — keep it simple for now
- Distributed tracing — single process, doesn't need it

## Notes

- `structlog.stdlib.ProcessorFormatter` is the bridge between structlog
  and stdlib logging handlers, needed for journald compatibility.
- Context managers must handle both success and exception cases. If the
  call times out, `outcome=timeout`. If exception, `outcome=error` and
  the exception is re-raised.
- Don't log entire prompts at INFO level — they're noisy. DEBUG is fine.
- The `llm_calls` table is the source of truth for "was the model
  behaving last night?" — keep writes there reliable even if the actual
  LLM call failed.
