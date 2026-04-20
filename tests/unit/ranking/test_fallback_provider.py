"""FallbackProvider composite — ADR-002 never-silently-to-claude."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from archive_agent.config import Config
from archive_agent.ranking import (
    FallbackProvider,
    TFIDFProvider,
    make_fallback_provider,
)
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    ContentType,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


class _ExplodingProvider:
    name = "ollama"

    async def health_check(self) -> HealthStatus:
        raise RuntimeError("boom")

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        raise RuntimeError("rank blew up")

    async def update_profile(
        self, current: TasteProfile, events: list[TasteEvent]
    ) -> TasteProfile:
        raise RuntimeError("update blew up")

    async def parse_search(self, query: str) -> SearchFilter:
        raise RuntimeError("parse blew up")


class _WorkingProvider:
    name = "ollama"

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok", detail="fine")

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        return [
            RankedCandidate(
                candidate=c,
                score=0.9,
                reasoning="primary result from working provider stub",
                rank=i + 1,
            )
            for i, c in enumerate(candidates[:n])
        ]

    async def update_profile(
        self, current: TasteProfile, events: list[TasteEvent]
    ) -> TasteProfile:
        return current.model_copy(update={"version": current.version + 1})

    async def parse_search(self, query: str) -> SearchFilter:
        return SearchFilter(keywords=["primary"])


def _candidate(archive_id: str) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.MOVIE,
        title=f"Film {archive_id}",
        year=1950,
        genres=["Noir"],
        source_collection="moviesandfilms",
        discovered_at=_NOW,
    )


# --- constructor validation -------------------------------------------------


def test_rejects_non_tfidf_fallback() -> None:
    primary = _WorkingProvider()
    # Any provider whose .name is not 'tfidf' must be refused.
    from archive_agent.ranking.ollama_provider import OllamaProvider  # noqa: F401

    # Use another WorkingProvider mock as a pseudo-claude just for the name check.
    class _FakeClaude(_WorkingProvider):
        name = "claude"

    with pytest.raises(ValueError, match="requires tfidf"):
        FallbackProvider(primary=primary, fallback=_FakeClaude())


# --- rank ------------------------------------------------------------------


async def test_primary_succeeds_no_fallback(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    primary = _WorkingProvider()
    composite = FallbackProvider(primary=primary, fallback=TFIDFProvider(conn=db))

    result = await composite.rank(
        TasteProfile(version=0, updated_at=_NOW),
        [_candidate("m1")],
        n=1,
    )

    assert result
    assert result[0].reasoning.startswith("primary result")


async def test_primary_fails_falls_through_to_tfidf(
    db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    composite = FallbackProvider(primary=_ExplodingProvider(), fallback=TFIDFProvider(conn=db))
    profile = TasteProfile(version=0, updated_at=_NOW, liked_genres=["Noir"])

    with caplog.at_level(logging.WARNING):
        result = await composite.rank(profile, [_candidate("m1")], n=1)

    assert result
    # TFIDFProvider reasoning carries its prefix — proof we fell through.
    assert result[0].reasoning.startswith("TF-IDF:")


async def test_empty_primary_result_is_valid(db: sqlite3.Connection) -> None:
    """Primary returning [] is not an error; no fallback."""
    q_candidates.upsert_candidate(db, _candidate("m1"))

    class _EmptyPrimary(_WorkingProvider):
        async def rank(self, *a: Any, **k: Any) -> list[RankedCandidate]:
            return []

    composite = FallbackProvider(primary=_EmptyPrimary(), fallback=TFIDFProvider(conn=db))
    result = await composite.rank(
        TasteProfile(version=0, updated_at=_NOW),
        [_candidate("m1")],
        n=1,
    )

    assert result == []


# --- other methods ---------------------------------------------------------


async def test_update_profile_falls_through(db: sqlite3.Connection) -> None:
    composite = FallbackProvider(primary=_ExplodingProvider(), fallback=TFIDFProvider(conn=db))
    current = TasteProfile(version=3, updated_at=_NOW)

    result = await composite.update_profile(current, [])

    # TFIDFProvider.update_profile increments the version.
    assert result.version == 4


async def test_parse_search_falls_through(db: sqlite3.Connection) -> None:
    composite = FallbackProvider(primary=_ExplodingProvider(), fallback=TFIDFProvider(conn=db))

    result = await composite.parse_search("40s noir")

    assert result.era == (1940, 1949)


async def test_health_check_falls_through(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    composite = FallbackProvider(primary=_ExplodingProvider(), fallback=TFIDFProvider(conn=db))

    result = await composite.health_check()

    assert result.status == "ok"
    assert result.detail and "corpus" in result.detail


# --- make_fallback_provider ------------------------------------------------


async def test_make_fallback_wraps_non_tfidf_primary(
    config: Config, db: sqlite3.Connection
) -> None:
    composite = make_fallback_provider("nightly_ranking", config, conn=db)
    # Ollama primary, tfidf fallback — a FallbackProvider instance.
    assert isinstance(composite, FallbackProvider)
    assert composite.name == "ollama"


async def test_make_fallback_bare_tfidf_when_primary_is_tfidf(
    config: Config, db: sqlite3.Connection
) -> None:
    config.llm.workflows.nightly_ranking = "tfidf"
    composite = make_fallback_provider("nightly_ranking", config, conn=db)
    # No wrapping — the primary is already the fallback target.
    assert isinstance(composite, TFIDFProvider)
