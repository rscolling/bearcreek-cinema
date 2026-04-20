"""FastAPI ``Depends(...)`` hooks.

Routes pull shared resources (config, DB connection, LLM provider)
through these. The lifespan in ``api.app`` stashes them on
``app.state``; tests can override via ``app.dependency_overrides``
without touching the lifespan at all.
"""

from __future__ import annotations

import sqlite3

from fastapi import Request

from archive_agent.config import Config
from archive_agent.ranking.provider import LLMProvider


def get_config(request: Request) -> Config:
    """Return the ``Config`` bound by the app lifespan."""
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        raise RuntimeError("app.state.config not set — lifespan didn't run?")
    return cfg  # type: ignore[no-any-return]


def get_db(request: Request) -> sqlite3.Connection:
    """Return the process-wide state DB connection."""
    conn = getattr(request.app.state, "db", None)
    if conn is None:
        raise RuntimeError("app.state.db not set — lifespan didn't run?")
    return conn  # type: ignore[no-any-return]


def get_provider(request: Request) -> LLMProvider:
    """Return the shared LLMProvider (``FallbackProvider`` in prod)."""
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        raise RuntimeError("app.state.provider not set — lifespan didn't run?")
    return provider  # type: ignore[no-any-return]


__all__ = ["get_config", "get_db", "get_provider"]
