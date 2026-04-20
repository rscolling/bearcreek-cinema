# phase4-01: FastAPI scaffold

## Goal

Stand up the FastAPI application skeleton that all subsequent endpoints
hang off. This card lands the `archive-agent serve` CLI, the `uvicorn`
entry point, the async lifespan that owns the shared state DB
connection + provider factory, and the logging middleware that emits
one structlog line per request.

No real endpoints yet — just `/` (a dumb "alive" probe). Health and
everything else land in follow-up cards.

## Prerequisites

- phase1-01 (scaffold), phase1-02 (config), phase1-03 (state DB),
  phase1-06 (structlog configuration)

## Inputs

- `CONTRACTS.md` §3 (HTTP API base URL, response shape, no auth in v1)
- ADR-011 (FastAPI + uvicorn, single-process)
- `config.api.{host, port}`

## Deliverables

1. `src/archive_agent/api/app.py`:

   ```python
   def create_app(config: Config) -> FastAPI:
       """Build the FastAPI instance — no network side effects here,
       just wire routers + middleware + lifespan. Called by the CLI
       entry point and by tests."""
   ```

   Responsibilities:
   - `lifespan` context manager opens the state DB + builds a shared
     `ranking.FallbackProvider` lazily on first use. Closes the DB on
     shutdown.
   - Attaches a structlog middleware that logs `event="http_request"`
     with method, path, status, latency_ms, client IP (LAN-only, OK
     to log).
   - Registers an exception handler that turns unhandled exceptions
     into `application/problem+json` responses without leaking the
     traceback to the client.

2. `src/archive_agent/api/dependencies.py`:

   ```python
   def get_config() -> Config: ...
   def get_db(request: Request) -> sqlite3.Connection: ...
   def get_provider(request: Request) -> LLMProvider: ...
   ```

   Every route depends on these via `Annotated[..., Depends(...)]`.
   Lifespan stashes them on `app.state` so tests can swap either by
   overriding `app.dependency_overrides`.

3. `src/archive_agent/api/routes/__init__.py` — just a pointer to each
   module (filled in by 4-02 onward). Empty `root.py` with:

   ```python
   @router.get("/")
   async def root() -> dict[str, str]:
       return {"name": "bear-creek-cinema-agent", "status": "alive"}
   ```

4. CLI:
   - Replace the `_not_implemented("serve")` stub with a real
     implementation that runs uvicorn against `create_app(cfg)` on
     `cfg.api.host:cfg.api.port`. Honor `--host` and `--port`
     overrides.

5. Tests in `tests/unit/api/`:
   - `test_app_build.py` — `create_app(config)` returns a FastAPI
     instance, `GET /` returns 200 with the expected JSON
   - `test_request_logging.py` — a request against the TestClient
     produces one structlog line at INFO with the expected keys
   - `test_exception_handler.py` — a route that raises returns
     problem+json with no traceback in the body

## Done when

- [ ] `archive-agent serve --port 8788` starts the API and `GET /`
  returns 200
- [ ] Every request emits one `http_request` log line
- [ ] Uncaught exceptions return problem+json, not a traceback
- [ ] `mypy --strict` passes on `archive_agent/api/`
- [ ] Unit tests pass

## Verification

```bash
archive-agent serve --port 8788 &
sleep 0.5
curl -s http://localhost:8788/ | jq
kill %1
pytest tests/unit/api/ -v
```

## Out of scope

- Real endpoints (/health, /recommendations, /search, etc.) — each
  lands in its own card.
- Auth — ADR-011 says LAN-only, no auth in v1.
- HTTPS — LAN-only; Tailscale handles transport if off-network.
- Rate limiting — endpoint-specific rate limits come in later cards
  (e.g., live Archive.org fallback in 4-08).

## Notes

- Async lifespan is the right seam for owning long-lived resources.
  Build the DB connection there, not in a module-level singleton, so
  the test fixture can build a fresh app per test.
- The structlog middleware should use `contextvars.bind_contextvars`
  so nested log lines from inside the route carry `request_id` without
  threading it through every function. `structlog.contextvars.clear_
  contextvars` on exit.
- Use `uvicorn.run` in the CLI path so Ctrl+C works cleanly. Don't
  spawn a thread.
