"""ADR-013 rating reader: latest-wins, Roku-sourced only."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from archive_agent.state.models import ContentType, TasteEvent, TasteEventKind
from archive_agent.state.queries import taste_events as q_taste_events
from archive_agent.taste.ratings import latest_for_all_shows, latest_for_show

_NOW = datetime(2026, 4, 19, tzinfo=UTC)


def _rating(
    show_id: str,
    kind: TasteEventKind,
    *,
    at: datetime,
    source: str = "roku_api",
    strength: float = 0.6,
) -> TasteEvent:
    return TasteEvent(
        timestamp=at,
        content_type=ContentType.SHOW,
        show_id=show_id,
        kind=kind,
        strength=strength,
        source=source,
    )


def test_latest_wins(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(days=10), strength=0.9)
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_UP, at=_NOW - timedelta(days=5))
    )
    q_taste_events.insert_event(
        db, _rating("s1", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(days=1), strength=1.0)
    )

    latest = latest_for_show(db, "s1")

    assert latest is not None
    assert latest.kind == TasteEventKind.RATED_LOVE


def test_non_roku_rating_events_are_ignored(db: sqlite3.Connection) -> None:
    # Someone mislabeled a playback event with a rating kind. Not surfaced.
    q_taste_events.insert_event(
        db,
        _rating(
            "s2",
            TasteEventKind.RATED_UP,
            at=_NOW - timedelta(days=1),
            source="playback",
        ),
    )
    assert latest_for_show(db, "s2") is None


def test_unrated_show_returns_none(db: sqlite3.Connection) -> None:
    assert latest_for_show(db, "never_rated") is None


def test_bulk_lookup_returns_newest_per_show(db: sqlite3.Connection) -> None:
    q_taste_events.insert_event(
        db, _rating("a", TasteEventKind.RATED_UP, at=_NOW - timedelta(days=10))
    )
    q_taste_events.insert_event(
        db, _rating("a", TasteEventKind.RATED_LOVE, at=_NOW - timedelta(days=2), strength=1.0)
    )
    q_taste_events.insert_event(
        db, _rating("b", TasteEventKind.RATED_DOWN, at=_NOW - timedelta(days=3), strength=0.9)
    )

    bulk = latest_for_all_shows(db)

    assert set(bulk.keys()) == {"a", "b"}
    assert bulk["a"].kind == TasteEventKind.RATED_LOVE
    assert bulk["b"].kind == TasteEventKind.RATED_DOWN


def test_same_timestamp_tiebreak_by_id(db: sqlite3.Connection) -> None:
    # Two rows at the exact same timestamp: higher id (newer insert) wins.
    same_ts = _NOW - timedelta(days=1)
    id_first = q_taste_events.insert_event(db, _rating("s3", TasteEventKind.RATED_UP, at=same_ts))
    id_second = q_taste_events.insert_event(
        db, _rating("s3", TasteEventKind.RATED_LOVE, at=same_ts, strength=1.0)
    )
    assert id_second > id_first

    latest = latest_for_show(db, "s3")

    assert latest is not None
    assert latest.kind == TasteEventKind.RATED_LOVE


def test_empty_db_bulk_returns_empty(db: sqlite3.Connection) -> None:
    assert latest_for_all_shows(db) == {}
