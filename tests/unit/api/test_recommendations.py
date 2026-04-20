"""/recommendations* endpoints.

Uses the real state DB from the lifespan, real ``latest_batch``
query, real serializer. Provider isn't invoked — the endpoints only
read the audit table.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.state.models import (
    Candidate,
    CandidateStatus,
    ContentType,
    RankedCandidate,
    ShowState,
    TasteEventKind,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import ranked_candidates as q_ranked
from archive_agent.state.queries import show_state as q_show_state

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(
    archive_id: str,
    title: str = "Film",
    *,
    content_type: ContentType = ContentType.MOVIE,
    runtime: int | None = 100,
    show_id: str | None = None,
    jf_item_id: str | None = None,
    **overrides: Any,
) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": content_type,
        "title": title,
        "year": 1950,
        "runtime_minutes": runtime,
        "genres": ["Noir"],
        "show_id": show_id,
        "source_collection": "moviesandfilms"
        if content_type == ContentType.MOVIE
        else "television",
        "discovered_at": _NOW,
        "jellyfin_item_id": jf_item_id,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _ranked(cand: Candidate, rank: int = 1) -> RankedCandidate:
    return RankedCandidate(
        candidate=cand,
        score=max(0.1, 1.0 - (rank - 1) * 0.1),
        reasoning=f"canned reasoning for rank {rank}",
        rank=rank,
    )


def _seed_batch(db: sqlite3.Connection, candidates: list[Candidate]) -> str:
    for c in candidates:
        q_candidates.upsert_candidate(db, c)
    picks = [_ranked(c, i + 1) for i, c in enumerate(candidates)]
    q_ranked.insert_batch(db, "batch1", picks, provider="ollama", profile_version=1, now=_NOW)
    return "batch1"


# --- GET /recommendations --------------------------------------------------


def test_list_returns_latest_batch(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(
            db,
            [
                _candidate("m1", "Film 1"),
                _candidate("m2", "Film 2"),
            ],
        )
        resp = client.get("/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["archive_id"] == "m1"
    assert first["poster_url"] == "/poster/m1"
    assert first["reasoning"].startswith("canned")


def test_list_filters_by_type(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(
            db,
            [
                _candidate("m1"),
                _candidate(
                    "sh1",
                    content_type=ContentType.SHOW,
                    show_id="showA",
                ),
            ],
        )
        movies = client.get("/recommendations?type=movie").json()
        shows = client.get("/recommendations?type=show").json()

    assert [i["archive_id"] for i in movies["items"]] == ["m1"]
    assert [i["archive_id"] for i in shows["items"]] == ["sh1"]


def test_list_respects_limit(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(db, [_candidate(f"m{i}") for i in range(5)])
        resp = client.get("/recommendations?limit=2")
    assert len(resp.json()["items"]) == 2


def test_list_empty_when_no_batch(app: FastAPI) -> None:
    with TestClient(app) as client:
        resp = client.get("/recommendations")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_list_surfaces_jellyfin_item_id_and_episodes_available(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        show = _candidate(
            "sh1",
            "A Show",
            content_type=ContentType.SHOW,
            show_id="showA",
            jf_item_id="jf-abc",
        )
        _seed_batch(db, [show])
        # show_state seed to exercise episodes_available
        q_show_state.upsert(
            db,
            ShowState(
                show_id="showA",
                episodes_finished=3,
                episodes_available=10,
                started_at=_NOW,
            ),
        )
        resp = client.get("/recommendations")

    item = resp.json()["items"][0]
    assert item["jellyfin_item_id"] == "jf-abc"
    assert item["episodes_available"] == 10


# --- GET /recommendations/for-tonight --------------------------------------


def test_for_tonight_caps_at_three(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(db, [_candidate(f"m{i}", runtime=95) for i in range(10)])
        resp = client.get("/recommendations/for-tonight")
    assert len(resp.json()["items"]) == 3


def test_for_tonight_returns_empty_without_batch(app: FastAPI) -> None:
    with TestClient(app) as client:
        resp = client.get("/recommendations/for-tonight")
    assert resp.json()["items"] == []


def test_for_tonight_evening_prefers_feature_length(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force hour=19 so the feature-length filter fires."""
    import archive_agent.api.routes.recommendations as mod

    real_datetime = mod.datetime

    class _FakeDatetime:
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_datetime(2026, 4, 20, 19, 0, 0, tzinfo=tz)

        @classmethod
        def __getattr__(cls, name: str) -> Any:  # pragma: no cover — passthrough
            return getattr(real_datetime, name)

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(
            db,
            [
                _candidate("short1", runtime=40),
                _candidate("long1", runtime=110),
                _candidate("long2", runtime=140),
                _candidate("long3", runtime=90),
            ],
        )
        resp = client.get("/recommendations/for-tonight")
    ids = [i["archive_id"] for i in resp.json()["items"]]
    assert "short1" not in ids
    assert set(ids) <= {"long1", "long2", "long3"}


def test_for_tonight_late_night_prefers_short(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import archive_agent.api.routes.recommendations as mod

    real_datetime = mod.datetime

    class _FakeDatetime:
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_datetime(2026, 4, 20, 23, 30, 0, tzinfo=tz)

        @classmethod
        def __getattr__(cls, name: str) -> Any:  # pragma: no cover
            return getattr(real_datetime, name)

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_batch(
            db,
            [
                _candidate("short1", runtime=30),
                _candidate("long1", runtime=120),
                _candidate("short2", runtime=55),
            ],
        )
        resp = client.get("/recommendations/for-tonight")
    ids = [i["archive_id"] for i in resp.json()["items"]]
    assert "long1" not in ids
    assert set(ids) <= {"short1", "short2"}


# --- reject / defer --------------------------------------------------------


def test_reject_writes_event_and_marks_candidate(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(db, _candidate("rj1"))
        resp = client.post("/recommendations/rj1/reject")
        assert resp.status_code == 204

        row = db.execute(
            "SELECT kind, archive_id FROM taste_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["kind"] == TasteEventKind.REJECTED.value
        assert row["archive_id"] == "rj1"
        cand = q_candidates.get_by_archive_id(db, "rj1")
        assert cand is not None
        assert cand.status == CandidateStatus.REJECTED


def test_defer_writes_event_without_changing_status(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        original = _candidate("df1")
        q_candidates.upsert_candidate(db, original)
        resp = client.post("/recommendations/df1/defer")
        assert resp.status_code == 204

        row = db.execute("SELECT kind FROM taste_events ORDER BY id DESC LIMIT 1").fetchone()
        assert row["kind"] == TasteEventKind.DEFERRED.value
        cand = q_candidates.get_by_archive_id(db, "df1")
        assert cand is not None
        assert cand.status == original.status  # unchanged


def test_reject_unknown_candidate_returns_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        resp = client.post("/recommendations/not-real/reject")
    assert resp.status_code == 404


def test_reject_on_episode_attributes_to_show(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(
            db,
            _candidate(
                "ep1",
                content_type=ContentType.EPISODE,
                show_id="showA",
                season=1,
                episode=1,
            ),
        )
        resp = client.post("/recommendations/ep1/reject")
        assert resp.status_code == 204

        row = db.execute(
            "SELECT content_type, show_id, archive_id FROM taste_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["content_type"] == "show"
        assert row["show_id"] == "showA"
        assert row["archive_id"] is None


# --- suppress unused import warnings --------------------------------------


_ = timedelta
