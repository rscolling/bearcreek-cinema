"""Bootstrap profile: gather + LLM call + ID preservation + insert."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    ContentType,
    SearchFilter,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import taste_events as q_taste_events
from archive_agent.state.queries import taste_profile_versions as q_profiles
from archive_agent.taste.bootstrap import (
    NoSignalError,
    ProfileExistsError,
    bootstrap_profile,
    gather_bootstrap_input,
)

_NOW = datetime(2026, 4, 19, tzinfo=UTC)


# --- helpers ----------------------------------------------------------------


def _movie_event(archive_id: str, kind: TasteEventKind, *, at: datetime) -> TasteEvent:
    return TasteEvent(
        timestamp=at,
        content_type=ContentType.MOVIE,
        archive_id=archive_id,
        kind=kind,
        strength=0.8,
        source="playback",
    )


def _show_event(show_id: str, kind: TasteEventKind, *, at: datetime) -> TasteEvent:
    return TasteEvent(
        timestamp=at,
        content_type=ContentType.SHOW,
        show_id=show_id,
        kind=kind,
        strength=0.8,
        source="playback",
    )


def _rating(show_id: str, kind: TasteEventKind, *, at: datetime) -> TasteEvent:
    return TasteEvent(
        timestamp=at,
        content_type=ContentType.SHOW,
        show_id=show_id,
        kind=kind,
        strength=1.0 if kind == TasteEventKind.RATED_LOVE else 0.6,
        source="roku_api",
    )


def _seed_movie_candidate(db: sqlite3.Connection, archive_id: str, title: str) -> None:
    q_candidates.upsert_candidate(
        db,
        Candidate(
            archive_id=archive_id,
            content_type=ContentType.MOVIE,
            title=title,
            year=1940,
            genres=["Noir"],
            source_collection="moviesandfilms",
            discovered_at=_NOW,
        ),
    )


def _seed_show_episode(db: sqlite3.Connection, show_id: str, show_title: str) -> None:
    q_candidates.upsert_candidate(
        db,
        Candidate(
            archive_id=f"{show_id}_ep1",
            content_type=ContentType.EPISODE,
            title=f"{show_title} S01E01",
            show_id=show_id,
            season=1,
            episode=1,
            genres=["Mystery"],
            source_collection="television",
            discovered_at=_NOW,
        ),
    )


class _FakeProvider:
    """In-memory LLMProvider that returns a canned profile."""

    name = "ollama"

    def __init__(self, canned: TasteProfile, *, raise_on_update: bool = False) -> None:
        self._canned = canned
        self._raise = raise_on_update
        self.captured_events: list[TasteEvent] | None = None

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")

    async def rank(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def update_profile(self, current: TasteProfile, events: list[TasteEvent]) -> TasteProfile:
        if self._raise:
            raise RuntimeError("LLM exploded")
        self.captured_events = events
        return self._canned.model_copy(update={"version": current.version + 1, "updated_at": _NOW})

    async def parse_search(self, query: str) -> SearchFilter:
        return SearchFilter()


# --- gather -----------------------------------------------------------------


def test_gather_buckets_events_correctly(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=10))
    )
    q_taste_events.insert_event(
        db, _movie_event("m2", TasteEventKind.REJECTED, at=_NOW - timedelta(days=9))
    )
    q_taste_events.insert_event(
        db, _show_event("s1", TasteEventKind.BINGE_POSITIVE, at=_NOW - timedelta(days=5))
    )
    q_taste_events.insert_event(
        db, _rating("s2", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(days=1))
    )

    inp = gather_bootstrap_input(db)

    assert len(inp.movie_events) == 2
    assert len(inp.show_events) == 1
    assert set(inp.ratings.keys()) == {"s2"}
    assert inp.ratings["s2"].kind == TasteEventKind.RATED_LOVE
    assert inp.total_events == 4


def test_gather_uses_latest_rating_per_show(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(days=5))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_UP, at=_NOW - timedelta(days=2))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(hours=1))
    )

    inp = gather_bootstrap_input(db)

    assert inp.ratings["s1"].kind == TasteEventKind.RATED_LOVE


def test_gather_resolves_titles_via_candidates(db: sqlite3.Connection) -> None:
    _seed_movie_candidate(db, "movie1", "Sample Movie")
    _seed_show_episode(db, "show1", "Sample Show")
    q_taste_events.insert_event(
        db, _movie_event("movie1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    q_taste_events.insert_event(
        db,
        _show_event("show1", TasteEventKind.BINGE_POSITIVE, at=_NOW - timedelta(days=1)),
    )

    inp = gather_bootstrap_input(db)

    assert "movie1" in inp.candidates_by_id
    assert inp.candidates_by_id["movie1"].title == "Sample Movie"
    assert "show1" in inp.candidates_by_id
    assert inp.candidates_by_id["show1"].show_id == "show1"


def test_gather_tolerates_phantom_ids(db: sqlite3.Connection) -> None:
    """Jellyfin-history events have archive_id='jellyfin:...' that isn't
    in candidates. Gather should succeed without mapping them."""
    q_taste_events.insert_event(
        db,
        _movie_event("jellyfin:abc123", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1)),
    )

    inp = gather_bootstrap_input(db)

    assert inp.movie_events[0].archive_id == "jellyfin:abc123"
    assert "jellyfin:abc123" not in inp.candidates_by_id


# --- bootstrap --------------------------------------------------------------


async def test_bootstrap_refuses_when_profile_exists(db: sqlite3.Connection) -> None:
    q_profiles.insert_profile(db, TasteProfile(version=1, updated_at=_NOW, summary="x"))
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(canned=TasteProfile(version=1, updated_at=_NOW))

    with pytest.raises(ProfileExistsError):
        await bootstrap_profile(db, provider)


async def test_bootstrap_force_replaces_existing(db: sqlite3.Connection) -> None:
    q_profiles.insert_profile(db, TasteProfile(version=1, updated_at=_NOW, summary="old"))
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(
        canned=TasteProfile(
            version=1,
            updated_at=_NOW,
            summary="fresh take",
            liked_genres=["Noir"],
        )
    )

    result = await bootstrap_profile(db, provider, force=True)

    assert result.version == 2  # existing was 1, new gets 2
    assert result.summary == "fresh take"


async def test_bootstrap_raises_when_no_signal(db: sqlite3.Connection) -> None:
    provider = _FakeProvider(canned=TasteProfile(version=1, updated_at=_NOW))
    with pytest.raises(NoSignalError):
        await bootstrap_profile(db, provider)


async def test_dry_run_does_not_insert(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(canned=TasteProfile(version=1, updated_at=_NOW, summary="dryrun"))

    result = await bootstrap_profile(db, provider, dry_run=True)

    assert result.summary == "dryrun"
    assert q_profiles.get_latest_profile(db) is None


async def test_bootstrap_preserves_ids_from_positive_signals(
    db: sqlite3.Connection,
) -> None:
    # LLM drops the liked_archive_ids it should have inferred from FINISHED.
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    q_taste_events.insert_event(
        db, _show_event("s1", TasteEventKind.BINGE_POSITIVE, at=_NOW - timedelta(days=1))
    )
    q_taste_events.insert_event(
        db, _movie_event("m2", TasteEventKind.REJECTED, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(
        canned=TasteProfile(
            version=1,
            updated_at=_NOW,
            summary="clean",
            # intentionally empty — preserve_ids must backfill
        )
    )

    result = await bootstrap_profile(db, provider)

    assert "m1" in result.liked_archive_ids
    assert "s1" in result.liked_show_ids
    assert "m2" in result.disliked_archive_ids


async def test_bootstrap_ratings_override_implicit_polarity(
    db: sqlite3.Connection,
) -> None:
    # Binge-negative says disliked; rating says loved — rating wins.
    q_taste_events.insert_event(
        db, _show_event("s1", TasteEventKind.BINGE_NEGATIVE, at=_NOW - timedelta(days=30))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(canned=TasteProfile(version=1, updated_at=_NOW, summary="x"))

    result = await bootstrap_profile(db, provider)

    assert "s1" in result.liked_show_ids
    assert "s1" not in result.disliked_show_ids


async def test_bootstrap_thumbs_down_always_disliked(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(
        canned=TasteProfile(
            version=1,
            updated_at=_NOW,
            summary="x",
            liked_show_ids=["s1"],  # LLM lied
        )
    )

    result = await bootstrap_profile(db, provider)

    assert "s1" in result.disliked_show_ids
    assert "s1" not in result.liked_show_ids


async def test_bootstrap_inserts_and_makes_result_retrievable(
    db: sqlite3.Connection,
) -> None:
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(days=1))
    )
    provider = _FakeProvider(canned=TasteProfile(version=1, updated_at=_NOW, summary="inserted!"))

    result = await bootstrap_profile(db, provider)

    assert result.version == 1
    latest = q_profiles.get_latest_profile(db)
    assert latest is not None
    assert latest.summary == "inserted!"
    assert latest.version == 1
