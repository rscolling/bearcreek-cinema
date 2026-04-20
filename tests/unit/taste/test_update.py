"""plan_update decision table + apply_update round-trip + run_if_due."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from archive_agent.config import TasteConfig
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    ContentType,
    SearchFilter,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.state.queries import taste_events as q_taste_events
from archive_agent.state.queries import taste_profile_versions as q_profiles
from archive_agent.taste.update import (
    apply_update,
    plan_update,
    run_if_due,
)

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _profile_inserted(db: sqlite3.Connection, *, when: datetime) -> TasteProfile:
    profile = TasteProfile(
        version=0,
        updated_at=when,
        summary="baseline",
        liked_genres=["Drama"],
    )
    q_profiles.insert_profile(db, profile)
    got = q_profiles.get_latest_profile(db)
    assert got is not None
    return got


def _movie_event(archive_id: str, kind: TasteEventKind, *, at: datetime) -> TasteEvent:
    return TasteEvent(
        timestamp=at,
        content_type=ContentType.MOVIE,
        archive_id=archive_id,
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


class _FakeProvider:
    name = "ollama"

    def __init__(self, returns: TasteProfile) -> None:
        self._returns = returns
        self.captured_events: list[TasteEvent] | None = None

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")

    async def rank(self, *a: Any, **k: Any) -> list[Any]:
        return []

    async def update_profile(
        self, current: TasteProfile, events: list[TasteEvent]
    ) -> TasteProfile:
        self.captured_events = events
        return self._returns.model_copy(
            update={"version": current.version + 1, "updated_at": _NOW}
        )

    async def parse_search(self, query: str) -> SearchFilter:
        return SearchFilter()


# --- plan_update decision table --------------------------------------------


def test_plan_no_profile_skips(db: sqlite3.Connection) -> None:
    plan = plan_update(db, TasteConfig(), now=_NOW)
    assert not plan.should_run
    assert plan.skip_reason is not None and "bootstrap" in plan.skip_reason


def test_plan_no_new_events_skips(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(days=2))
    plan = plan_update(db, TasteConfig(), now=_NOW)
    assert not plan.should_run
    assert plan.skip_reason is not None and "no events" in plan.skip_reason


def test_plan_rate_limited_skips(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(hours=3))
    for i in range(10):
        q_taste_events.insert_event(
            db, _movie_event(f"m{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=1))
        )
    plan = plan_update(db, TasteConfig(update_interval_hours=24), now=_NOW)
    assert not plan.should_run
    assert plan.skip_reason is not None and "rate-limited" in plan.skip_reason


def test_plan_too_few_events_skips(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(days=2))
    for i in range(2):  # below default min of 5
        q_taste_events.insert_event(
            db, _movie_event(f"m{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=1))
        )
    plan = plan_update(db, TasteConfig(), now=_NOW)
    assert not plan.should_run
    assert plan.skip_reason is not None and "below threshold" in plan.skip_reason


def test_plan_force_bypasses_rate_limit(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(hours=1))
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(minutes=10))
    )
    plan = plan_update(db, TasteConfig(), now=_NOW, force=True)
    assert plan.should_run
    assert plan.events_to_send


def test_plan_dedupes_ratings_to_latest_per_show(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(days=2))
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(hours=10))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_UP, at=_NOW - timedelta(hours=5))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(hours=1))
    )
    # Padding to pass the min-events gate
    for i in range(5):
        q_taste_events.insert_event(
            db, _movie_event(f"m{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=2))
        )

    plan = plan_update(db, TasteConfig(), now=_NOW)

    assert plan.should_run
    ratings_in_send = [e for e in plan.events_to_send if e.show_id == "s1"]
    assert len(ratings_in_send) == 1
    assert ratings_in_send[0].kind == TasteEventKind.RATED_LOVE


def test_plan_caps_at_max_events(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(days=2))
    for i in range(20):
        q_taste_events.insert_event(
            db, _movie_event(f"m{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=i + 1))
        )
    plan = plan_update(db, TasteConfig(max_events_per_update=5), now=_NOW)
    assert plan.should_run
    assert len(plan.events_to_send) == 5
    assert plan.truncated == 15
    # Newest-first means the ones closer to _NOW survive.
    times = [e.timestamp for e in plan.events_to_send]
    assert times == sorted(times, reverse=True)


# --- apply_update + run_if_due ---------------------------------------------


async def test_apply_update_inserts_new_version(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(days=2))
    for i in range(10):
        q_taste_events.insert_event(
            db, _movie_event(f"m{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=1))
        )
    plan = plan_update(db, TasteConfig(), now=_NOW)
    provider = _FakeProvider(
        returns=TasteProfile(version=1, updated_at=_NOW, summary="updated")
    )

    result = await apply_update(db, provider, plan)

    assert result.version == 2  # was 1, bumped
    assert result.summary == "updated"
    latest = q_profiles.get_latest_profile(db)
    assert latest is not None and latest.version == 2


async def test_apply_update_preserves_liked_ids(db: sqlite3.Connection) -> None:
    baseline = TasteProfile(
        version=0,
        updated_at=_NOW - timedelta(days=2),
        summary="baseline",
        liked_archive_ids=["prev1"],
        liked_show_ids=["prevshow"],
    )
    q_profiles.insert_profile(db, baseline)
    q_taste_events.insert_event(
        db, _movie_event("new1", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=1))
    )
    for i in range(4):  # pad to pass min-events
        q_taste_events.insert_event(
            db, _movie_event(f"p{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=2))
        )

    plan = plan_update(db, TasteConfig(), now=_NOW)
    # LLM drops all IDs
    provider = _FakeProvider(
        returns=TasteProfile(version=1, updated_at=_NOW, summary="fresh")
    )

    result = await apply_update(db, provider, plan)

    assert "prev1" in result.liked_archive_ids
    assert "prevshow" in result.liked_show_ids
    assert "new1" in result.liked_archive_ids


async def test_apply_update_ratings_override(db: sqlite3.Connection) -> None:
    baseline = TasteProfile(
        version=0,
        updated_at=_NOW - timedelta(days=2),
        liked_show_ids=["showA"],
    )
    q_profiles.insert_profile(db, baseline)
    # Newest rating flips the show to thumbs-down.
    q_taste_events.insert_event(
        db, _rating("showA", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(hours=1))
    )
    for i in range(4):  # pad
        q_taste_events.insert_event(
            db, _movie_event(f"p{i}", TasteEventKind.FINISHED, at=_NOW - timedelta(hours=2))
        )

    plan = plan_update(db, TasteConfig(), now=_NOW)
    provider = _FakeProvider(
        returns=TasteProfile(version=1, updated_at=_NOW, liked_show_ids=["showA"])
    )

    result = await apply_update(db, provider, plan)

    assert "showA" in result.disliked_show_ids
    assert "showA" not in result.liked_show_ids


async def test_apply_rejects_non_runnable_plan(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(hours=1))
    plan = plan_update(db, TasteConfig(), now=_NOW)  # will skip: rate-limited
    provider = _FakeProvider(returns=TasteProfile(version=1, updated_at=_NOW))
    with pytest.raises(ValueError):
        await apply_update(db, provider, plan)


async def test_run_if_due_returns_none_when_skipped(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(hours=1))
    provider = _FakeProvider(returns=TasteProfile(version=1, updated_at=_NOW))
    result = await run_if_due(db, provider, TasteConfig(), now=_NOW)
    assert result is None


async def test_run_if_due_applies_when_force(db: sqlite3.Connection) -> None:
    _profile_inserted(db, when=_NOW - timedelta(hours=1))
    q_taste_events.insert_event(
        db, _movie_event("m1", TasteEventKind.FINISHED, at=_NOW - timedelta(minutes=5))
    )
    provider = _FakeProvider(
        returns=TasteProfile(version=1, updated_at=_NOW, summary="after force")
    )

    result = await run_if_due(db, provider, TasteConfig(), now=_NOW, force=True)

    assert result is not None
    assert result.summary == "after force"
