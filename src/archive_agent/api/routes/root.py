"""Liveness probe. Every other endpoint lives in its own module."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def root() -> dict[str, str]:
    """Confirm the process is alive. No subsystem checks — see ``/health``."""
    return {"name": "bear-creek-cinema-agent", "status": "alive"}


__all__ = ["router"]
