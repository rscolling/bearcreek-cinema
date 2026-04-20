"""/poster/{archive_id} — cache hit/miss, 404s, 502s, eviction."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)

# Tiny valid JPEG (just a 1x1 magic-number stub; FastAPI + clients
# don't decode bytes, they just serve them).
_FAKE_JPEG = bytes.fromhex("ffd8ffd9")


def _candidate(
    archive_id: str,
    *,
    poster_url: str | None = "https://example.test/poster.jpg",
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.MOVIE,
        title=f"T {archive_id}",
        year=1950,
        genres=["Drama"],
        poster_url=poster_url,
        source_collection="moviesandfilms",
        discovered_at=_NOW,
    )


class _FakeHttpxResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = _FAKE_JPEG,
        content_type: str = "image/jpeg",
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.test/poster.jpg")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f"{self.status_code}", request=request, response=response)


class _FakeHttpxClient:
    def __init__(
        self,
        behavior: Any,
        counter: dict[str, int],
        *,
        timeout: float = 10.0,
    ) -> None:
        self._behavior = behavior
        self._counter = counter

    async def __aenter__(self) -> _FakeHttpxClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeHttpxResponse:
        self._counter["calls"] += 1
        if callable(self._behavior):
            import inspect

            result = self._behavior(url)
            if inspect.isawaitable(result):
                return await result  # type: ignore[no-any-return]
            return result  # type: ignore[no-any-return]
        return self._behavior


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: Any = None,
    raise_exc: Exception | None = None,
) -> dict[str, int]:
    counter = {"calls": 0}

    def _factory(*args: Any, **kwargs: Any) -> _FakeHttpxClient:
        if raise_exc is not None:

            async def _raise(_: Any) -> Any:
                raise raise_exc

            return _FakeHttpxClient(_raise, counter)
        resp = response or _FakeHttpxResponse()
        return _FakeHttpxClient(resp, counter)

    import archive_agent.api.routes.poster as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)
    return counter


# --- cache hit / miss ------------------------------------------------------


def test_cache_miss_then_hit(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    counts = _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("p1"))

        # Miss → hits upstream.
        first = client.get("/poster/p1")
        assert first.status_code == 200
        assert first.content == _FAKE_JPEG
        assert first.headers["content-type"].startswith("image/jpeg")
        assert counts["calls"] == 1

        # Second call served from disk cache.
        second = client.get("/poster/p1")
        assert second.status_code == 200
        assert second.content == _FAKE_JPEG
        assert counts["calls"] == 1  # no new upstream fetch


def test_cache_writes_to_state_dir(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("p1"))
        client.get("/poster/p1")

    cache_dir = app.state.config.paths.state_db.parent / "poster_cache"
    files = list(cache_dir.iterdir())
    assert any(f.name.startswith("p1.") for f in files)


def test_content_type_picks_extension(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeHttpxResponse(content=b"PNG", content_type="image/png"),
    )
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("pp"))
        client.get("/poster/pp")

    cache_dir = app.state.config.paths.state_db.parent / "poster_cache"
    assert (cache_dir / "pp.png").exists()


# --- 404s -----------------------------------------------------------------


def test_unknown_archive_id_returns_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/poster/not-real")
    assert resp.status_code == 404


def test_null_poster_url_returns_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("np", poster_url=None))
        resp = client.get("/poster/np")
    assert resp.status_code == 404


# --- upstream failures ----------------------------------------------------


def test_upstream_timeout_returns_502(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, raise_exc=httpx.TimeoutException("slow"))
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("t1"))
        resp = client.get("/poster/t1")
    assert resp.status_code == 502
    assert "retry-after" in (k.lower() for k in resp.headers)


def test_upstream_error_returns_502(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeHttpxResponse(status_code=503, content_type="text/html"),
    )
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("e1"))
        resp = client.get("/poster/e1")
    assert resp.status_code == 502


# --- eviction -------------------------------------------------------------


def test_oldest_access_eviction_when_over_budget(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 600 KB images * 3 files = 1.8 MB total, over the 1 MB budget.
    # Oldest-accessed entries evict first.
    app.state.config.api.poster_cache_size_mb = 1  # default truthy
    cache_dir = app.state.config.paths.state_db.parent / "poster_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Seed 3 cached entries with staggered atimes.
    for i in range(3):
        p = cache_dir / f"old{i}.jpg"
        p.write_bytes(b"x" * (600 * 1024))  # 600 KB each → 1.8 MB total
        os_stat_before = p.stat()
        # Space atimes 1s apart.
        new_atime = time.time() - (3 - i) * 10
        import os as _os

        _os.utime(p, (new_atime, os_stat_before.st_mtime))

    app.state.config.api.poster_cache_size_mb = 1  # 1 MB budget

    _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("new1"))
        # Miss → fetch → prune.
        resp = client.get("/poster/new1")
        assert resp.status_code == 200

    remaining = sorted(p.name for p in cache_dir.iterdir() if p.is_file())
    # The oldest-accessed "old0.jpg" should be gone first.
    assert "old0.jpg" not in remaining
    # Newest entry we just wrote survived.
    assert any(name.startswith("new1.") for name in remaining)


# --- Cache-Control headers ------------------------------------------------


def test_response_sets_cache_control(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("cc1"))
        miss = client.get("/poster/cc1")
        hit = client.get("/poster/cc1")

    assert "max-age=86400" in miss.headers["cache-control"]
    assert "max-age=86400" in hit.headers["cache-control"]


# --- unused import suppression ----------------------------------------------


_ = Path
