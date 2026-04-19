"""enrich_candidate preserves Archive.org fields + fills gaps."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from archive_agent.metadata import TmdbClient, enrich_candidate, enrich_new_candidates
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates

from .conftest import (
    CONFIGURATION_RESPONSE,
    GET_MOVIE_RESPONSE,
    GET_SHOW_RESPONSE,
    SEARCH_MOVIE_RESPONSE,
    SEARCH_TV_RESPONSE,
)


def _candidate(**overrides: Any) -> Candidate:
    defaults: dict[str, Any] = dict(
        archive_id="sita-sings-the-blues",
        content_type=ContentType.MOVIE,
        title="Sita Sings the Blues",
        year=2008,
        source_collection="moviesandfilms",
        status=CandidateStatus.NEW,
        discovered_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Candidate(**defaults)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in self.responses:
            v = self.responses[path]
            if callable(v):
                return v(request)
            return httpx.Response(200, json=v)
        return httpx.Response(404, json={"status_code": 34})


async def _make_client(db: sqlite3.Connection, responses: dict[str, Any]) -> TmdbClient:
    client = TmdbClient(SecretStr("k"), db)
    await client.__aenter__()
    await client._client.aclose()  # type: ignore[union-attr]
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.themoviedb.org/3",
        transport=_MockTransport(responses),
    )
    return client


_MOVIE_RESPONSES: dict[str, Any] = {
    "/3/search/movie": SEARCH_MOVIE_RESPONSE,
    "/3/movie/22660": GET_MOVIE_RESPONSE,
    "/3/configuration": CONFIGURATION_RESPONSE,
}

_SHOW_RESPONSES: dict[str, Any] = {
    "/3/search/tv": SEARCH_TV_RESPONSE,
    "/3/tv/1433": GET_SHOW_RESPONSE,
    "/3/configuration": CONFIGURATION_RESPONSE,
}


async def test_enrich_fills_empty_movie_fields(db: sqlite3.Connection) -> None:
    client = await _make_client(db, _MOVIE_RESPONSES)
    empty = _candidate()
    enriched = await enrich_candidate(empty, client)
    assert enriched.genres == ["animation", "music"]
    assert enriched.runtime_minutes == 82
    assert enriched.description.startswith("A detailed retelling")
    assert enriched.poster_url == "https://image.tmdb.org/t/p/w342/abc123.jpg"
    await client.__aexit__(None, None, None)


async def test_enrich_does_not_overwrite_existing(db: sqlite3.Connection) -> None:
    """Archive.org's curated fields win if they're populated."""
    client = await _make_client(db, _MOVIE_RESPONSES)
    existing = _candidate(
        genres=["archive-genre"],
        runtime_minutes=99,
        description="Archive.org's own description",
        poster_url="https://archive.example/poster.jpg",
    )
    enriched = await enrich_candidate(existing, client)
    assert enriched.genres == ["archive-genre"]
    assert enriched.runtime_minutes == 99
    assert enriched.description == "Archive.org's own description"
    assert enriched.poster_url == "https://archive.example/poster.jpg"
    await client.__aexit__(None, None, None)


async def test_enrich_show_uses_episode_run_time(db: sqlite3.Connection) -> None:
    client = await _make_client(db, _SHOW_RESPONSES)
    episode = _candidate(
        archive_id="dvds-s01e01",
        content_type=ContentType.EPISODE,
        title="The Dick Van Dyke Show",
        year=1961,
        source_collection="television",
    )
    enriched = await enrich_candidate(episode, client)
    # episode_run_time[0] is 25 in the fixture
    assert enriched.runtime_minutes == 25
    assert enriched.genres == ["comedy"]
    await client.__aexit__(None, None, None)


async def test_enrich_returns_unchanged_on_no_match(db: sqlite3.Connection) -> None:
    client = await _make_client(
        db, {"/3/search/movie": {"page": 1, "total_results": 0, "results": []}}
    )
    before = _candidate()
    after = await enrich_candidate(before, client)
    assert after is before  # identity, not just equality — no TMDb hit, no copy
    await client.__aexit__(None, None, None)


async def test_enrich_new_candidates_skips_already_complete(db: sqlite3.Connection) -> None:
    # Seed two candidates: one missing fields, one already filled
    needy = _candidate(archive_id="needy", status=CandidateStatus.NEW)
    complete = _candidate(
        archive_id="complete",
        status=CandidateStatus.NEW,
        genres=["archive"],
        description="Archive description",
        poster_url="https://archive.example/p.jpg",
        runtime_minutes=90,
    )
    q_candidates.upsert_candidate(db, needy)
    q_candidates.upsert_candidate(db, complete)

    client = await _make_client(db, _MOVIE_RESPONSES)
    result = await enrich_new_candidates(db, client)
    # Only the needy one is SELECTed (it has empty genres/poster/description)
    assert result.seen == 1
    assert result.updated == 1
    await client.__aexit__(None, None, None)


async def test_enrich_new_candidates_continues_after_failure(db: sqlite3.Connection) -> None:
    good = _candidate(archive_id="good")
    bad = _candidate(archive_id="bad", title="Unknown Title Nothing Will Find")
    q_candidates.upsert_candidate(db, good)
    q_candidates.upsert_candidate(db, bad)

    # Good matches; bad returns empty results (TMDb miss, not an error)
    responses: dict[str, Any] = {
        "/3/search/movie": lambda req: httpx.Response(
            200,
            json=SEARCH_MOVIE_RESPONSE
            if req.url.params.get("query") == "Sita Sings the Blues"
            else {"page": 1, "total_results": 0, "results": []},
        ),
        "/3/movie/22660": GET_MOVIE_RESPONSE,
        "/3/configuration": CONFIGURATION_RESPONSE,
    }
    client = await _make_client(db, responses)
    result = await enrich_new_candidates(db, client)
    assert result.seen == 2
    assert result.updated == 1
    assert result.missing_tmdb_match == 1
    assert result.failed == 0
    await client.__aexit__(None, None, None)
