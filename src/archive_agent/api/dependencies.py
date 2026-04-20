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
from archive_agent.ranking.tfidf import TFIDFIndex


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


def get_tfidf_index(request: Request) -> TFIDFIndex:
    """Lazy-build + cache the TF-IDF index on ``app.state``.

    Rebuilding for every ``/search/similar`` would cost ~100ms at
    10k candidates; one-shot build amortizes across the lifetime of
    the app. Refreshes are the daemon loop's concern (it rebuilds
    after big discovery sweeps and swaps the cached attribute).
    """
    index = getattr(request.app.state, "tfidf_index", None)
    if index is None:
        conn = get_db(request)
        index = TFIDFIndex.build(conn)
        request.app.state.tfidf_index = index
    return index


__all__ = ["get_config", "get_db", "get_provider", "get_tfidf_index"]
