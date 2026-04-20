"""``GET /health`` — subsystem health for the Roku app.

Always returns 200 with a ``status`` body field. Load balancers should
not treat a degraded subsystem as a 5xx; the client decides how to
render it.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from archive_agent.api.dependencies import get_config, get_db
from archive_agent.api.subsystems import SubsystemReport, gather_health
from archive_agent.config import Config

router = APIRouter()


@router.get("/health", response_model=SubsystemReport)
async def health(
    config: Annotated[Config, Depends(get_config)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SubsystemReport:
    return await gather_health(config, conn)


__all__ = ["router"]
