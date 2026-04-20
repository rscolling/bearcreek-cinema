# phase4-07: `/disk` endpoint

## Goal

Expose the librarian's per-zone disk usage + budget headroom over
HTTP. Same source of truth as `archive-agent librarian status` — the
Roku settings screen shows a "disk" panel, and the API keeps that in
sync.

## Prerequisites

- phase4-01 (scaffold)
- phase2-05 (librarian `budget_report`)

## Inputs

- `CONTRACTS.md` §3 `/disk` shape + `ZoneUsage` model
- `librarian.budget_report(config)` from phase 2

## Deliverables

1. `src/archive_agent/api/routes/disk.py`:

   ```python
   @router.get("/disk")
   async def disk(
       config: Annotated[Config, Depends(get_config)],
   ) -> DiskReport: ...
   ```

2. `DiskReport` (mirrors `CONTRACTS.md` §3 exactly):

   ```python
   class DiskReport(BaseModel):
       zones: list[ZoneUsage]
       budget_gb: int
       used_gb: float
       headroom_gb: float
   ```

3. Adapter that turns `librarian.budget_report` output into
   `DiskReport`:
   - Bytes → GB (1e9, not 1<<30; match `librarian status` output)
   - Skip zones the user-owned `/media/movies` zone from the used/
     budget calculation (it's outside the budget — ADR + existing
     librarian code already knows this)
   - Round GB values to 1 decimal

4. Tests in `tests/unit/api/test_disk.py`:
   - Populated zones → expected DiskReport
   - Missing zone directories → 200 with used=0 for those zones (no
     crash)
   - Budget exceeded → headroom_gb is negative, no special status
     field (the client decides what to render)

## Done when

- [ ] `curl /disk` returns the DiskReport schema
- [ ] `archive-agent librarian status` output matches the endpoint
  response (GB values, zone counts)
- [ ] Missing zone dirs don't crash the endpoint
- [ ] Unit tests pass, mypy clean

## Verification

```bash
curl -s http://localhost:8788/disk | jq
archive-agent librarian status
# cross-check the numbers match
```

## Out of scope

- Historical usage chart — phase 6 dashboard.
- Alerting when near budget — the Roku will render a warning stripe
  when `used_gb / budget_gb > 0.9`; that's client-side.

## Notes

- `budget_report` is synchronous and cheap — no need to cache the
  result. If this endpoint ever starts appearing in flame graphs,
  revisit.
- Don't expose absolute paths to the client. `ZoneUsage.path`
  currently includes the full filesystem path; consider whether
  that's OK or whether we should emit only the zone name. Current
  stance: keep the path since the Roku never displays it; it's for
  debugging. Revisit if the UI shows it by accident.
