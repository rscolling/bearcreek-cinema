"""CRUD for the ``candidates`` table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from archive_agent.state.models import Candidate, CandidateStatus, ContentType


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        archive_id=row["archive_id"],
        content_type=ContentType(row["content_type"]),
        title=row["title"],
        year=row["year"],
        runtime_minutes=row["runtime_minutes"],
        show_id=row["show_id"],
        season=row["season"],
        episode=row["episode"],
        total_episodes_known=row["total_episodes_known"],
        genres=json.loads(row["genres"]),
        description=row["description"],
        poster_url=row["poster_url"],
        formats_available=json.loads(row["formats_available"]),
        size_bytes=row["size_bytes"],
        source_collection=row["source_collection"],
        status=CandidateStatus(row["status"]),
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        jellyfin_item_id=row["jellyfin_item_id"],
    )


def upsert_candidate(conn: sqlite3.Connection, candidate: Candidate) -> None:
    """Insert a new candidate or overwrite an existing one (by archive_id)."""
    conn.execute(
        """
        INSERT INTO candidates (
            archive_id, content_type, title, year, runtime_minutes,
            show_id, season, episode, total_episodes_known,
            genres, description, poster_url, formats_available,
            size_bytes, source_collection, status, discovered_at, jellyfin_item_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(archive_id) DO UPDATE SET
            content_type=excluded.content_type,
            title=excluded.title,
            year=excluded.year,
            runtime_minutes=excluded.runtime_minutes,
            show_id=excluded.show_id,
            season=excluded.season,
            episode=excluded.episode,
            total_episodes_known=excluded.total_episodes_known,
            genres=excluded.genres,
            description=excluded.description,
            poster_url=excluded.poster_url,
            formats_available=excluded.formats_available,
            size_bytes=excluded.size_bytes,
            source_collection=excluded.source_collection,
            status=excluded.status,
            discovered_at=excluded.discovered_at,
            jellyfin_item_id=excluded.jellyfin_item_id
        """,
        (
            candidate.archive_id,
            candidate.content_type.value,
            candidate.title,
            candidate.year,
            candidate.runtime_minutes,
            candidate.show_id,
            candidate.season,
            candidate.episode,
            candidate.total_episodes_known,
            json.dumps(candidate.genres),
            candidate.description,
            candidate.poster_url,
            json.dumps(candidate.formats_available),
            candidate.size_bytes,
            candidate.source_collection,
            candidate.status.value,
            candidate.discovered_at.isoformat(),
            candidate.jellyfin_item_id,
        ),
    )
    conn.commit()


def get_by_archive_id(conn: sqlite3.Connection, archive_id: str) -> Candidate | None:
    row = conn.execute("SELECT * FROM candidates WHERE archive_id = ?", (archive_id,)).fetchone()
    return _row_to_candidate(row) if row is not None else None


def list_by_status(
    conn: sqlite3.Connection, status: CandidateStatus, *, limit: int | None = None
) -> list[Candidate]:
    sql = "SELECT * FROM candidates WHERE status = ? ORDER BY discovered_at DESC"
    params: tuple[object, ...] = (status.value,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (status.value, limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_candidate(r) for r in rows]


def list_by_show(conn: sqlite3.Connection, show_id: str) -> list[Candidate]:
    """Return every candidate (typically EPISODE) rows for a given
    show, ordered by (season, episode). Used by the TV sampler to
    figure out what's available, what's downloaded, and what's next."""
    rows = conn.execute(
        "SELECT * FROM candidates WHERE show_id = ? "
        "ORDER BY season ASC NULLS LAST, episode ASC NULLS LAST",
        (show_id,),
    ).fetchall()
    return [_row_to_candidate(r) for r in rows]


def update_status(conn: sqlite3.Connection, archive_id: str, status: CandidateStatus) -> bool:
    """Return True if exactly one row was updated."""
    cur = conn.execute(
        "UPDATE candidates SET status = ? WHERE archive_id = ?",
        (status.value, archive_id),
    )
    conn.commit()
    return cur.rowcount == 1
