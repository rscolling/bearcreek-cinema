"""CRUD round-trip for the ``taste_events`` table."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from archive_agent.state.models import ContentType, TasteEvent, TasteEventKind
from archive_agent.state.queries import taste_events as q


def test_insert_movie_event_returns_id(db: sqlite3.Connection) -> None:
    event = TasteEvent(
        timestamp=datetime.now(UTC),
        content_type=ContentType.MOVIE,
        archive_id="sita_sings_the_blues",
        kind=TasteEventKind.FINISHED,
        strength=0.95,
    )
    rowid = q.insert_event(db, event)
    assert rowid >= 1


def test_list_since_filters_by_timestamp(db: sqlite3.Connection) -> None:
    old = TasteEvent(
        timestamp=datetime.now(UTC) - timedelta(days=60),
        content_type=ContentType.MOVIE,
        archive_id="old_movie",
        kind=TasteEventKind.FINISHED,
        strength=1.0,
    )
    recent = TasteEvent(
        timestamp=datetime.now(UTC) - timedelta(hours=1),
        content_type=ContentType.SHOW,
        show_id="recent_show",
        kind=TasteEventKind.BINGE_POSITIVE,
        strength=0.8,
    )
    q.insert_event(db, old)
    q.insert_event(db, recent)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    rows = q.list_since(db, cutoff)
    assert len(rows) == 1
    assert rows[0].show_id == "recent_show"
    assert rows[0].kind == TasteEventKind.BINGE_POSITIVE


def test_check_constraint_rejects_both_ids_null(db: sqlite3.Connection) -> None:
    import pytest

    # Bypass Pydantic's validator and hit the SQL CHECK directly
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO taste_events (timestamp, content_type, kind, strength, source)
            VALUES ('2026-01-01T00:00:00+00:00', 'movie', 'finished', 0.9, 'playback')
            """
        )


def test_check_constraint_rejects_episode_content_type(db: sqlite3.Connection) -> None:
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO taste_events (timestamp, content_type, archive_id, kind, strength, source)
            VALUES ('2026-01-01T00:00:00+00:00', 'episode', 'x', 'finished', 0.9, 'playback')
            """
        )
