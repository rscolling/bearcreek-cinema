"""FastAPI app factory for the Bear Creek Cinema HTTP service.

``create_app(config)`` is the one place that wires routers,
middleware, exception handling, and the lifespan. The CLI
(``archive-agent serve``) and tests both go through it — no other
construction path exists.

Lifespan owns:

- The state DB connection. Opened on startup via
  ``state.db.connect`` and closed on shutdown. Routes pull it from
  ``app.state.db`` through ``get_db`` (``api.dependencies``).
- The shared ``LLMProvider`` (``FallbackProvider`` wrapping the
  configured primary). Built once so the TF-IDF index inside it
  stays warm across requests.

Middleware logs one ``http_request`` structlog event per request with
method, path, status, latency, and the LAN client IP — which is fine
to log because the API is LAN-only (ADR-011, no auth in v1).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from archive_agent.api.routes.health import router as health_router
from archive_agent.api.routes.root import router as root_router
from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.ranking.factory import make_fallback_provider
from archive_agent.state.db import close_db, connect, reset_cached_db
from archive_agent.state.migrations import apply_pending

_log = get_logger("archive_agent.api")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the DB + build the provider, release them on shutdown."""
    cfg: Config = app.state.config
    conn = connect(cfg.paths.state_db)
    apply_pending(conn)
    provider = make_fallback_provider("nightly_ranking", cfg, conn=conn)
    app.state.db = conn
    app.state.provider = provider
    _log.info("api_started", host=cfg.api.host, port=cfg.api.port)
    try:
        yield
    finally:
        conn.close()
        # Drop the module-level DB singleton in case it was populated
        # by CLI side-imports — prevents test fixtures tripping on a
        # stale handle if the process sticks around (e.g., uvicorn
        # autoreload).
        close_db()
        reset_cached_db()
        _log.info("api_stopped")


async def _request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Any]],
) -> Any:
    """Emit one structlog line per request with timing + status."""
    request_id = uuid.uuid4().hex[:12]
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    client = request.client.host if request.client else "-"
    try:
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=latency_ms,
            client=client,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log.error(
            "http_request_error",
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            client=client,
            error=type(exc).__name__,
        )
        raise
    finally:
        structlog.contextvars.clear_contextvars()


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Return ``application/problem+json`` for anything that escapes a route.

    Deliberately doesn't include the traceback — the client is the
    Roku app, and internal stacks don't belong on the wire.
    """
    _log.error(
        "api_unhandled_exception",
        path=request.url.path,
        error=type(exc).__name__,
        detail=str(exc),
    )
    body = {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": f"{type(exc).__name__}: {exc}",
    }
    return JSONResponse(
        content=body,
        status_code=500,
        media_type="application/problem+json",
    )


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI application with routers + middleware.

    No network side effects until the lifespan runs. Tests can
    ``TestClient(create_app(cfg))`` and get a fresh app per test.
    """
    app = FastAPI(
        title="Bear Creek Cinema",
        version="0.1.0",
        lifespan=_lifespan,
    )
    # Attach config to app.state before the lifespan runs so the
    # dependency helpers work in tests that bypass the lifespan.
    app.state.config = config

    app.middleware("http")(_request_logging_middleware)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    app.include_router(root_router)
    app.include_router(health_router)

    return app


__all__ = ["create_app"]
