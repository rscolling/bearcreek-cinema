# phase4-02: `/health` endpoint

## Goal

Expose the same subsystem health report that `archive-agent health all`
prints, but over HTTP so the Roku app can tell the user "agent is up"
or "agent can't reach Jellyfin" without shelling in.

## Prerequisites

- phase4-01 (FastAPI scaffold)
- phase1-06 (health CLI — reuse the underlying gather)

## Inputs

- `CONTRACTS.md` §3: response shape
- Existing `health all` implementation in `archive_agent.__main__`
  (health of ollama / jellyfin / state_db / disk)

## Deliverables

1. Extract the gather logic out of `__main__.py:health_all` into
   `src/archive_agent/api/subsystems.py`:

   ```python
   class SubsystemReport(BaseModel):
       status: Literal["ok", "degraded", "down"]
       ollama: dict[str, str]
       jellyfin: dict[str, str]
       claude: dict[str, str] | None  # omitted when not configured
       state_db: dict[str, str | int]
       disk: dict[str, float | int | str]

   async def gather_health(config: Config, conn: sqlite3.Connection) -> SubsystemReport
   ```

   CLI `health all` now calls `gather_health` + formats for stdout;
   the endpoint reuses the same function.

2. `src/archive_agent/api/routes/health.py`:

   ```python
   @router.get("/health")
   async def health(
       config: Annotated[Config, Depends(get_config)],
       conn: Annotated[sqlite3.Connection, Depends(get_db)],
   ) -> SubsystemReport: ...
   ```

3. Status aggregation:
   - `ok` — every subsystem is `ok`
   - `degraded` — anything is `degraded` AND nothing is `down`
   - `down` — anything is `down`
   - Missing Claude config is not a failure; the field just isn't
     emitted.

4. Tests in `tests/unit/api/test_health.py`:
   - All subsystems ok → 200, status=ok
   - Ollama down → 200, status=down, `ollama.status=down`
   - Claude not configured → no `claude` key in response
   - State DB missing schema_version table → still responds, with
     state_db.status=down

## Done when

- [ ] `curl http://localhost:8788/health | jq` returns the report
- [ ] `archive-agent health all` output matches the endpoint response
  field-by-field
- [ ] Aggregate status rolls up correctly for every combination
- [ ] Unit tests pass, mypy clean

## Verification

```bash
curl -s http://localhost:8788/health | jq
# stop ollama, re-check:
docker compose -f infra/ollama/docker-compose.yaml stop
curl -s http://localhost:8788/health | jq '.status'   # "down"
docker compose -f infra/ollama/docker-compose.yaml start
pytest tests/unit/api/test_health.py -v
```

## Out of scope

- Prometheus metrics endpoint — phase 6 if at all.
- Auth — LAN-only.

## Notes

- Keep health checks cheap: this will get polled by the Roku app on
  launch. A full Jellyfin auth round trip is fine (~100ms) but don't
  call `provider.rank` or anything expensive.
- Timeouts matter: cap individual subsystem checks at 2s. Callers
  infer "down" from the timeout, not from a blown response.
- The endpoint never returns non-200 for a subsystem being down —
  that confuses load balancers that treat 5xx specially. Instead,
  the 200 body's `status` field tells the truth.
