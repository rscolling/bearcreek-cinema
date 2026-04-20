"""``GET /disk`` — zone-by-zone disk usage + budget headroom.

Thin adapter over ``librarian.budget_report``: the librarian owns
the byte-counting logic, this module just flattens the result into
the GB-denominated wire shape the Roku settings screen expects.

Clients never display the filesystem paths (they're for debugging),
but CONTRACTS.md §3 keeps ``path`` in ``ZoneUsage``, so we pass it
through rather than mask it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from archive_agent.api.dependencies import get_config
from archive_agent.config import Config
from archive_agent.librarian import budget_report

router = APIRouter()

_BYTES_PER_GB = 1e9


class ZoneUsage(BaseModel):
    zone: Literal["movies", "tv", "recommendations", "tv-sampler"]
    path: str
    used_gb: float
    file_count: int


class DiskReport(BaseModel):
    zones: list[ZoneUsage]
    budget_gb: int
    used_gb: float
    headroom_gb: float


def _to_gb(n: int) -> float:
    return round(n / _BYTES_PER_GB, 1)


@router.get("/disk", response_model=DiskReport)
async def disk(
    config: Annotated[Config, Depends(get_config)],
) -> DiskReport:
    report = budget_report(config)
    zones = [
        ZoneUsage(
            zone=u.zone.value,
            path=str(u.path),
            used_gb=_to_gb(u.used_bytes),
            file_count=u.file_count,
        )
        for u in report.zones
    ]
    return DiskReport(
        zones=zones,
        budget_gb=config.librarian.max_disk_gb,
        used_gb=_to_gb(report.agent_used_bytes),
        headroom_gb=_to_gb(report.headroom_bytes),
    )


__all__ = ["DiskReport", "ZoneUsage", "router"]
