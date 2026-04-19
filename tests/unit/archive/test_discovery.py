"""Candidate mapping + discover() end-to-end with a fake search iterator."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from archive_agent.archive import discovery
from archive_agent.archive.discovery import (
    DiscoverResult,
    _merge_status,
    search_result_to_candidate,
)
from archive_agent.archive.search import ArchiveSearchResult
from archive_agent.config import (
    ApiConfig,
    ArchiveConfig,
    Config,
    JellyfinConfig,
    LibrarianConfig,
    LlmClaudeConfig,
    LlmConfig,
    LlmOllamaConfig,
    LlmWorkflowsConfig,
    LoggingConfig,
    PathsConfig,
    TmdbConfig,
)
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending
from archive_agent.state.models import CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            state_db=tmp_path / "state.db",
            media_movies=tmp_path / "movies",
            media_tv=tmp_path / "tv",
            media_recommendations=tmp_path / "rec",
            media_tv_sampler=tmp_path / "sampler",
        ),
        jellyfin=JellyfinConfig(
            url="http://localhost:8096",
            api_key=SecretStr("k"),
            user_id="u",
        ),
        archive=ArchiveConfig(min_download_count=100, year_from=1920, year_to=2000),
        tmdb=TmdbConfig(api_key=SecretStr("t")),
        llm=LlmConfig(
            workflows=LlmWorkflowsConfig(),
            ollama=LlmOllamaConfig(),
            claude=LlmClaudeConfig(),
        ),
        librarian=LibrarianConfig(),
        api=ApiConfig(),
        logging=LoggingConfig(),
    )


def _result(
    identifier: str = "id-1",
    *,
    year: int | None = 1949,
    downloads: int | None = 10_000,
    subject: list[str] | None = None,
) -> ArchiveSearchResult:
    return ArchiveSearchResult(
        identifier=identifier,
        title=f"Title {identifier}",
        mediatype="movies",
        year=year,
        downloads=downloads,
        runtime_minutes=100,
        subject=subject if subject is not None else ["drama", "FILM-NOIR"],
        description="desc",
        formats=["h.264"],
    )


def test_search_result_to_candidate_for_movie() -> None:
    c = search_result_to_candidate(_result(), source_collection="moviesandfilms")
    assert c.archive_id == "id-1"
    assert c.content_type is ContentType.MOVIE
    assert c.source_collection == "moviesandfilms"
    # Genres normalized to lowercase + deduped + sorted
    assert c.genres == ["drama", "film-noir"]
    assert c.status is CandidateStatus.NEW


def test_search_result_to_candidate_for_tv_is_episode() -> None:
    c = search_result_to_candidate(_result("ep-1"), source_collection="television")
    assert c.content_type is ContentType.EPISODE


def test_genres_are_deduped_and_lowercased() -> None:
    r = _result(subject=["Drama", "drama", "DRAMA", " Drama "])
    c = search_result_to_candidate(r, source_collection="moviesandfilms")
    assert c.genres == ["drama"]


def test_merge_status_preserves_progressed_status() -> None:
    fresh = search_result_to_candidate(_result(), source_collection="moviesandfilms")
    existing = fresh.model_copy(update={"status": CandidateStatus.APPROVED})
    merged = _merge_status(existing, fresh)
    assert merged.status is CandidateStatus.APPROVED


def test_merge_status_with_no_existing_returns_fresh() -> None:
    fresh = search_result_to_candidate(_result(), source_collection="moviesandfilms")
    assert _merge_status(None, fresh) is fresh


# --- discover() end-to-end with a fake search ---


class _FakeSearch:
    """Replays a fixed list of ArchiveSearchResults and records which
    queries were requested."""

    def __init__(self, by_collection: dict[str, list[ArchiveSearchResult]]) -> None:
        self._by_collection = by_collection
        self.calls: list[str] = []

    async def __call__(
        self,
        collection: str,
        *,
        min_downloads: int,
        year_from: int,
        year_to: int,
        limit: int | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[ArchiveSearchResult]:
        self.calls.append(collection)
        for r in self._by_collection.get(collection, []):
            yield r


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> _FakeSearch:
    fake = _FakeSearch(
        {
            "moviesandfilms": [
                _result("movie-1"),
                _result("movie-2", year=1910),  # out of year range
                _result("movie-3", downloads=10),  # below quality floor
            ],
            "television": [
                _result("ep-1"),
                _result("ep-2"),
            ],
        }
    )
    monkeypatch.setattr(discovery, "search_collection", fake)
    return fake


async def test_discover_inserts_rows(
    db: sqlite3.Connection, config: Config, fake_search: _FakeSearch
) -> None:
    result = await discovery.discover(db, config, collection="both")
    assert isinstance(result, DiscoverResult)
    assert result.inserted == 3  # 1 movie + 2 eps (2 movies rejected)
    assert result.skipped_year == 1
    assert result.skipped_quality == 1
    assert result.by_collection == {"moviesandfilms": 1, "television": 2}
    assert fake_search.calls == ["moviesandfilms", "television"]

    rows = db.execute(
        "SELECT archive_id, content_type FROM candidates ORDER BY archive_id"
    ).fetchall()
    assert [(r["archive_id"], r["content_type"]) for r in rows] == [
        ("ep-1", "episode"),
        ("ep-2", "episode"),
        ("movie-1", "movie"),
    ]


async def test_discover_is_idempotent(
    db: sqlite3.Connection, config: Config, fake_search: _FakeSearch
) -> None:
    first = await discovery.discover(db, config, collection="both")
    second = await discovery.discover(db, config, collection="both")
    assert first.inserted == 3
    assert first.updated == 0
    assert second.inserted == 0
    assert second.updated == 3


async def test_discover_preserves_progressed_status(
    db: sqlite3.Connection, config: Config, fake_search: _FakeSearch
) -> None:
    await discovery.discover(db, config, collection="both")
    q_candidates.update_status(db, "movie-1", CandidateStatus.APPROVED)
    await discovery.discover(db, config, collection="both")
    row = q_candidates.get_by_archive_id(db, "movie-1")
    assert row is not None
    assert row.status is CandidateStatus.APPROVED


async def test_discover_single_collection(
    db: sqlite3.Connection, config: Config, fake_search: _FakeSearch
) -> None:
    result = await discovery.discover(db, config, collection="moviesandfilms")
    assert fake_search.calls == ["moviesandfilms"]
    assert result.by_collection == {"moviesandfilms": 1}
