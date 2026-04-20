"""/search (baseline title) + /search/similar + /search/autocomplete."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.state.models import (
    Candidate,
    CandidateStatus,
    ContentType,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import taste_profile_versions as q_profiles

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(archive_id: str, title: str, **overrides: Any) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": ContentType.MOVIE,
        "title": title,
        "year": 1950,
        "runtime_minutes": 95,
        "genres": ["Noir"],
        "description": "",
        "source_collection": "moviesandfilms",
        "discovered_at": _NOW,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _seed_catalog(db: sqlite3.Connection) -> None:
    items = [
        _candidate("third_man", "The Third Man", description="Postwar Vienna noir."),
        _candidate("thin_man", "The Thin Man", description="Witty detective comedy."),
        _candidate(
            "detour",
            "Detour",
            description="Poverty-row noir road trip.",
        ),
        _candidate(
            "beverly_hillbillies",
            "The Beverly Hillbillies",
            description="Sitcom family.",
            content_type=ContentType.SHOW,
            show_id="bh01",
            source_collection="television",
        ),
    ]
    for c in items:
        q_candidates.upsert_candidate(db, c)


# --- /search ---------------------------------------------------------------


def test_search_title_returns_matches(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.post("/search", json={"query": "third man"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "title"
    assert body["filter"] is None
    assert body["items"], body
    top = body["items"][0]
    assert top["archive_id"] == "third_man"
    assert top["match_reason"] == "title match"
    assert top["poster_url"] == "/poster/third_man"
    assert 0.0 < top["relevance_score"] <= 1.0


def test_search_no_match_returns_empty_items(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.post("/search", json={"query": "zzqqxx_no_match", "limit": 5})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_search_type_filter_restricts_pool(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.post("/search", json={"query": "the", "type": "show", "limit": 10})
    body = resp.json()
    assert body["items"]
    assert all(i["content_type"] == "show" for i in body["items"])


def test_search_empty_query_is_rejected(app: FastAPI) -> None:
    with TestClient(app) as client:
        resp = client.post("/search", json={"query": ""})
    # Pydantic validates min_length=1 → 422.
    assert resp.status_code == 422


def test_search_result_status_reflects_jellyfin_item_id(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        q_candidates.upsert_candidate(
            db,
            _candidate("ready1", "Ready One", jellyfin_item_id="jf-x"),
        )
        q_candidates.upsert_candidate(db, _candidate("not_yet", "Ready One Too"))
        resp = client.post("/search", json={"query": "Ready"})

    items = {i["archive_id"]: i for i in resp.json()["items"]}
    assert items["ready1"]["status"] == "ready"
    assert items["not_yet"]["status"] == "downloadable"


# --- /search/similar ------------------------------------------------------


def test_similar_returns_matches_excluding_anchor(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.post(
            "/search/similar",
            json={"anchor_archive_id": "third_man", "limit": 5},
        )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [i["archive_id"] for i in items]
    assert "third_man" not in ids
    # Another noir should lead since they share genres + era bucket.
    assert "detour" in ids or "thin_man" in ids
    assert all(i["match_reason"].startswith("similar to") for i in items)


def test_similar_unknown_anchor_returns_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.post(
            "/search/similar",
            json={"anchor_archive_id": "nonexistent", "limit": 5},
        )
    assert resp.status_code == 404


def test_similar_excludes_disliked_archive_ids(app: FastAPI) -> None:
    with TestClient(app) as client:
        db: sqlite3.Connection = app.state.db
        _seed_catalog(db)
        # Profile dislikes detour explicitly.
        q_profiles.insert_profile(
            db,
            TasteProfile(
                version=0,
                updated_at=_NOW,
                disliked_archive_ids=["detour"],
            ),
        )
        resp = client.post(
            "/search/similar",
            json={"anchor_archive_id": "third_man", "limit": 10},
        )
    ids = [i["archive_id"] for i in resp.json()["items"]]
    assert "detour" not in ids


# --- /search/autocomplete -------------------------------------------------


def test_autocomplete_prefix_matches(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.get("/search/autocomplete?q=the+t&limit=5")
    assert resp.status_code == 200
    suggestions = resp.json()["suggestions"]
    titles = {s["title"] for s in suggestions}
    assert titles & {"The Third Man", "The Thin Man"}


def test_autocomplete_empty_prefix_returns_empty(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.get("/search/autocomplete?q=")
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []


def test_autocomplete_respects_limit(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        resp = client.get("/search/autocomplete?q=the&limit=2")
    assert len(resp.json()["suggestions"]) <= 2


# --- tfidf_index cache ---------------------------------------------------


def test_tfidf_index_is_cached_across_similar_calls(app: FastAPI) -> None:
    """First /similar call builds; second reuses the cached instance."""
    with TestClient(app) as client:
        _seed_catalog(app.state.db)
        client.post("/search/similar", json={"anchor_archive_id": "third_man"})
        index_first = app.state.tfidf_index

        client.post("/search/similar", json={"anchor_archive_id": "thin_man"})
        index_second = app.state.tfidf_index

    # Same object → cache reused.
    assert index_first is index_second


# --- keep unused imports from being pruned ------------------------------


_ = CandidateStatus
