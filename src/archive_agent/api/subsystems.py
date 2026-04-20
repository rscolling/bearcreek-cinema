"""Subsystem health gather — shared by the CLI and the HTTP endpoint.

One source of truth for "is the agent healthy?". Callers:

- ``archive-agent health all`` formats the report for a terminal.
- ``GET /health`` returns it as JSON to the Roku app.

Runs Ollama / Jellyfin / (optional) Claude probes in parallel, adds
state DB + disk checks (which are cheap and synchronous), and folds
the per-subsystem statuses into an aggregate.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from archive_agent.config import Config
from archive_agent.jellyfin.client import JellyfinClient
from archive_agent.ranking.factory import make_provider
from archive_agent.state.migrations import current_version


class SubsystemReport(BaseModel):
    status: Literal["ok", "degraded", "down"]
    ollama: dict[str, Any]
    jellyfin: dict[str, Any]
    claude: dict[str, Any] | None = None
    state_db: dict[str, Any] = Field(default_factory=dict)
    disk: dict[str, Any] = Field(default_factory=dict)


async def _probe_ollama(config: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        provider = make_provider("ollama", config, conn=conn)
        status = await provider.health_check()
        return status.model_dump()
    except Exception as exc:
        return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}


async def _probe_jellyfin(config: Config) -> dict[str, Any]:
    try:
        async with JellyfinClient(
            config.jellyfin.url,
            config.jellyfin.api_key,
            config.jellyfin.user_id,
        ) as client:
            info = await client.ping()
            await client.authenticate()
            return {
                "status": "ok",
                "version": info.version,
                "server_name": info.server_name,
            }
    except Exception as exc:
        return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}


async def _probe_claude(
    config: Config, conn: sqlite3.Connection
) -> dict[str, Any] | None:
    if config.llm.claude.api_key is None:
        return None
    try:
        provider = make_provider("claude", config, conn=conn)
        status = await provider.health_check()
        return status.model_dump()
    except Exception as exc:
        return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}


def _probe_state_db(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        return {"status": "ok", "schema_version": current_version(conn)}
    except Exception as exc:
        return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}


def _probe_disk(config: Config) -> dict[str, Any]:
    # Peek at one of the zone mounts just to verify the filesystem is
    # reachable — the detailed budget view lives at ``GET /disk``.
    used_bytes = 0
    for path in (
        config.paths.media_movies,
        config.paths.media_tv,
        config.paths.media_recommendations,
        config.paths.media_tv_sampler,
    ):
        if path.exists():
            usage = shutil.disk_usage(path)
            used_bytes = max(used_bytes, usage.used)
    return {
        "status": "ok",
        "used_gb": round(used_bytes / 1e9, 2),
        "budget_gb": config.librarian.max_disk_gb,
    }


def _rollup(*subsystems: dict[str, Any] | None) -> Literal["ok", "degraded", "down"]:
    """Aggregate: any ``down`` → down; else any ``degraded`` → degraded;
    else ``ok``. Missing subsystems (``None``) are ignored."""
    statuses = [s.get("status") for s in subsystems if s is not None]
    if any(s == "down" for s in statuses):
        return "down"
    if any(s == "degraded" for s in statuses):
        return "degraded"
    return "ok"


async def gather_health(
    config: Config, conn: sqlite3.Connection
) -> SubsystemReport:
    """Run all probes and fold into a ``SubsystemReport``."""
    ollama, jellyfin, claude = await asyncio.gather(
        _probe_ollama(config, conn),
        _probe_jellyfin(config),
        _probe_claude(config, conn),
    )
    state_db = _probe_state_db(conn)
    disk = _probe_disk(config)
    status = _rollup(ollama, jellyfin, claude, state_db, disk)
    return SubsystemReport(
        status=status,
        ollama=ollama,
        jellyfin=jellyfin,
        claude=claude,
        state_db=state_db,
        disk=disk,
    )


__all__ = ["SubsystemReport", "gather_health"]
