"""/search endpoint with router dispatch (phase4-08).

Covers: title path (phase4-05 still works), descriptive intent,
more-like-X anchor resolution, play-command verb stripping, and
the SearchResponse.intent surface field.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(
    archive_id: str,
    title: str,
    *,
    genres: list[str] | None = None,
    description: str = "",
    content_type: ContentType = ContentType.MOVIE,
    year: int = 1950,
    jf_item_id: str | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        runtime_minutes=95,
        genres=genres or ["Drama"],
        description=description,
        source_collection="moviesandfilms" if content_type == ContentType.MOVIE else "television",
        discovered_at=_NOW,
        jellyfin_item_id=jf_item_id,
    )


def _seed(db: sqlite3.Connection) -> None:
    for c in [
        _candidate(
            "third_man",
            "The Third Man",
            genres=["Noir", "Thriller"],
            description="Postwar Vienna noir.",
        ),
        _candidate(
            "thin_man",
            "The Thin Man",
            genres=["Comedy", "Mystery"],
            description="Witty detective comedy.",
        ),
        _candidate(
            "noir_detour",
            "Detour",
            genres=["Noir"],
            description="Poverty-row noir road trip.",
        ),
        _candidate(
            "funny_comedy",
            "Some Funny Comedy",
            genres=["Comedy"],
            description="Screwball household romance.",
        ),
        _candidate(
            "doc",
            "Nature Notes",
            genres=["Documentary"],
            description="A documentary about wilderness.",
        ),
        _candidate(
            "nosferatu",
            "Nosferatu",
            genres=["Horror"],
            description="Silent Expressionist vampire film.",
            year=1922,
        ),
    ]:
        q_candidates.upsert_candidate(db, c)


# --- title intent (regression on phase4-05 behavior) ----------------------


def test_title_query_dispatches_to_fts(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post("/search", json={"query": "third man", "limit": 5})
    body = resp.json()
    assert body["intent"] == "title"
    assert next(i["archive_id"] for i in body["items"]) == "third_man"
    assert body["items"][0]["match_reason"] == "title match"


# --- descriptive intent ---------------------------------------------------


def test_descriptive_query_dispatches_to_tfidf(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post("/search", json={"query": "something noir", "limit": 5})
    body = resp.json()
    assert body["intent"] == "descriptive"
    # A noir film should top the list; at minimum, noir items come back.
    ids = [i["archive_id"] for i in body["items"]]
    assert any(aid in {"third_man", "noir_detour"} for aid in ids)
    # match_reason carries the genre overlap explicitly when found.
    assert any(
        i["match_reason"].startswith("matches") for i in body["items"]
    )


def test_descriptive_short_documentary(app: FastAPI) -> None:
    """Multi-term descriptive query lands on the descriptive pipeline."""
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search", json={"query": "short documentary", "limit": 5}
        )
    body = resp.json()
    assert body["intent"] == "descriptive"
    ids = [i["archive_id"] for i in body["items"]]
    assert "doc" in ids


# --- more like X ---------------------------------------------------------


def test_more_like_resolves_anchor_and_runs_similar(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search", json={"query": "more like the third man", "limit": 5}
        )
    body = resp.json()
    assert body["intent"] == "descriptive"
    assert body["items"], body
    # Anchor (third_man) must be excluded; reason is "similar to ...".
    assert "third_man" not in [i["archive_id"] for i in body["items"]]
    assert all(i["match_reason"].startswith("similar to") for i in body["items"])


def test_more_like_with_unresolvable_anchor_returns_empty(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search",
            json={"query": "more like totally-made-up-title-here", "limit": 5},
        )
    body = resp.json()
    assert body["intent"] == "descriptive"
    assert body["items"] == []


# --- play command --------------------------------------------------------


def test_play_command_strips_verb_and_runs_title_match(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search", json={"query": "play The Third Man", "limit": 5}
        )
    body = resp.json()
    assert body["intent"] == "play"
    ids = [i["archive_id"] for i in body["items"]]
    assert "third_man" in ids
    assert body["items"][0]["match_reason"] == "play-command title match"


# --- default title fallback ----------------------------------------------


def test_ambiguous_multi_word_defaults_to_title(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search",
            json={"query": "seven words no heuristic match here!", "limit": 5},
        )
    body = resp.json()
    assert body["intent"] == "title"  # default_title fallback
    # Empty items is legitimate — nothing matches.
    assert isinstance(body["items"], list)


# --- /similar preserved --------------------------------------------------


def test_similar_endpoint_still_works(app: FastAPI) -> None:
    with TestClient(app) as client:
        _seed(app.state.db)
        resp = client.post(
            "/search/similar",
            json={"anchor_archive_id": "third_man", "limit": 5},
        )
    body = resp.json()
    ids = [i["archive_id"] for i in body["items"]]
    assert "third_man" not in ids
    assert body["items"], body


# --- suppress unused import warnings --------------------------------------


_ = Any
