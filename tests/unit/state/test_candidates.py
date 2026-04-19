"""CRUD round-trip for the ``candidates`` table."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q


def _movie(
    archive_id: str = "sita_sings_the_blues",
    *,
    status: CandidateStatus = CandidateStatus.NEW,
    title: str = "Sita Sings the Blues",
    genres: list[str] | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.MOVIE,
        title=title,
        year=2008,
        runtime_minutes=82,
        genres=genres or ["animation", "music"],
        description="A musical retelling of the Ramayana.",
        formats_available=["h264", "mpeg4"],
        size_bytes=1_234_567_890,
        source_collection="moviesandfilms",
        status=status,
        discovered_at=datetime.now(UTC),
    )


def test_insert_and_fetch(db: sqlite3.Connection) -> None:
    c = _movie()
    q.upsert_candidate(db, c)
    fetched = q.get_by_archive_id(db, c.archive_id)
    assert fetched is not None
    assert fetched.title == c.title
    assert fetched.genres == c.genres
    assert fetched.formats_available == c.formats_available
    assert fetched.status == CandidateStatus.NEW
    assert fetched.content_type == ContentType.MOVIE


def test_get_missing_returns_none(db: sqlite3.Connection) -> None:
    assert q.get_by_archive_id(db, "does-not-exist") is None


def test_upsert_overwrites_by_archive_id(db: sqlite3.Connection) -> None:
    q.upsert_candidate(db, _movie(title="Original"))
    q.upsert_candidate(db, _movie(title="Updated"))
    fetched = q.get_by_archive_id(db, "sita_sings_the_blues")
    assert fetched is not None
    assert fetched.title == "Updated"
    # Only one row for that archive_id
    count = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert count == 1


def test_list_by_status_filters_and_orders(db: sqlite3.Connection) -> None:
    q.upsert_candidate(db, _movie("a", status=CandidateStatus.NEW))
    q.upsert_candidate(db, _movie("b", status=CandidateStatus.RANKED))
    q.upsert_candidate(db, _movie("c", status=CandidateStatus.NEW))
    news = q.list_by_status(db, CandidateStatus.NEW)
    assert {c.archive_id for c in news} == {"a", "c"}
    rankeds = q.list_by_status(db, CandidateStatus.RANKED)
    assert [c.archive_id for c in rankeds] == ["b"]


def test_update_status_true_when_row_exists(db: sqlite3.Connection) -> None:
    q.upsert_candidate(db, _movie())
    assert q.update_status(db, "sita_sings_the_blues", CandidateStatus.APPROVED) is True
    fetched = q.get_by_archive_id(db, "sita_sings_the_blues")
    assert fetched is not None
    assert fetched.status == CandidateStatus.APPROVED


def test_update_status_false_when_row_missing(db: sqlite3.Connection) -> None:
    assert q.update_status(db, "missing", CandidateStatus.APPROVED) is False


def test_source_collection_constraint(db: sqlite3.Connection) -> None:
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO candidates (
                archive_id, content_type, title, genres, formats_available,
                source_collection, status, discovered_at
            ) VALUES ('x', 'movie', 't', '[]', '[]', 'nonsense', 'new', '2026-01-01T00:00:00+00:00')
            """
        )
