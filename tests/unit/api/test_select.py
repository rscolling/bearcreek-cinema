"""/recommendations/{id}/select and /shows/{id}/commit endpoints."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(archive_id: str, **overrides: Any) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": ContentType.MOVIE,
        "title": f"T {archive_id}",
        "year": 1950,
        "runtime_minutes": 95,
        "genres": ["Drama"],
        "source_collection": "moviesandfilms",
        "discovered_at": _NOW,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved_item_id: str | None = "jf-id",
    download_status: str = "done",
    step_show_action: str = "start_sampling",
) -> None:
    import archive_agent.commands.select as mod
    from archive_agent.archive.downloader import DownloadResult
    from archive_agent.librarian.placement import PlaceResult
    from archive_agent.librarian.tv_sampler import SamplerResult

    async def _download(req: Any, conn: Any, **_: Any) -> DownloadResult:
        return DownloadResult(
            archive_id=req.archive_id,
            status=download_status,  # type: ignore[arg-type]
            file_path=Path("/tmp/fake.mkv"),
            size_bytes=100,
        )

    def _place(
        conn: Any, config: Any, *, candidate: Candidate, source_path: Path, zone: Any, **_: Any
    ) -> PlaceResult:
        return PlaceResult(
            archive_id=candidate.archive_id,
            zone=zone,
            source_path=source_path,
            dest_path=source_path,
            moved=True,
            size_bytes=0,
        )

    async def _resolve(
        config: Any, archive_id: str, zone: Any, conn: Any
    ) -> str | None:
        return resolved_item_id

    async def _step_show(
        conn: Any, config: Any, show_id: str, downloader: Any, **_: Any
    ) -> SamplerResult:
        return SamplerResult(
            show_id=show_id,
            action=step_show_action,  # type: ignore[arg-type]
            reason="stubbed",
        )

    monkeypatch.setattr(mod, "download_one", _download)
    monkeypatch.setattr(mod, "place", _place)
    monkeypatch.setattr(mod, "_resolve_after_placement", _resolve)
    monkeypatch.setattr(mod, "step_show", _step_show)


# --- /select ---------------------------------------------------------------


def test_select_ready_returns_200(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, resolved_item_id="jf-abc")
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("m1"))
        resp = client.post("/recommendations/m1/select", json={"play": True})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["jellyfin_item_id"] == "jf-abc"


def test_select_queued_returns_202(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, resolved_item_id=None)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("m1"))
        resp = client.post("/recommendations/m1/select", json={"play": True})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"


def test_select_failed_returns_502(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, download_status="failed")
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("m1"))
        resp = client.post("/recommendations/m1/select", json={"play": True})

    assert resp.status_code == 502


def test_select_unknown_candidate_returns_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/recommendations/nope/select", json={"play": True})
    assert resp.status_code == 404


def test_select_accepts_empty_body(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Body defaults to play=true when omitted."""
    _patch_pipeline(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("m1"))
        resp = client.post("/recommendations/m1/select")
    assert resp.status_code == 200


# --- /shows/{id}/commit ----------------------------------------------------


def test_commit_returns_202_with_estimates(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        for i in range(1, 4):
            q_candidates.upsert_candidate(
                db,
                _candidate(
                    f"ep{i}",
                    content_type=ContentType.EPISODE,
                    show_id="showZ",
                    season=1,
                    episode=i,
                    size_bytes=1_500_000_000,
                ),
            )
        resp = client.post("/shows/showZ/commit")

    assert resp.status_code == 202
    body = resp.json()
    assert body["show_id"] == "showZ"
    assert body["enqueued_downloads"] == 3
    assert body["estimated_gb"] == pytest.approx(4.5)


def test_commit_unknown_show_returns_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/shows/nothing/commit")
    assert resp.status_code == 404
