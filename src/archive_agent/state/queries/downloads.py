"""CRUD for the ``downloads`` table — Archive.org transfer records."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

DownloadStatus = Literal["queued", "downloading", "done", "failed", "aborted"]
Zone = Literal["movies", "tv", "recommendations", "tv-sampler"]

_ACTIVE = ("queued", "downloading")


def insert(
    conn: sqlite3.Connection,
    archive_id: str,
    zone: Zone,
    *,
    path: str | None = None,
    size_bytes: int | None = None,
) -> int:
    """Create a queued download row and return its id."""
    cur = conn.execute(
        """
        INSERT INTO downloads (archive_id, zone, path, size_bytes, status, started_at)
        VALUES (?, ?, ?, ?, 'queued', NULL)
        """,
        (archive_id, zone, path, size_bytes),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT produced no lastrowid — schema drift?")
    return int(rowid)


def update_progress(
    conn: sqlite3.Connection,
    download_id: int,
    *,
    status: DownloadStatus,
    path: str | None = None,
    size_bytes: int | None = None,
    error: str | None = None,
) -> None:
    """Advance a download row's status. Sets started_at/finished_at based
    on the transition."""
    now = datetime.now(UTC).isoformat()
    sets: list[str] = ["status = ?"]
    params: list[object] = [status]
    if status == "downloading":
        sets.append("started_at = COALESCE(started_at, ?)")
        params.append(now)
    if status in ("done", "failed", "aborted"):
        sets.append("finished_at = ?")
        params.append(now)
    if path is not None:
        sets.append("path = ?")
        params.append(path)
    if size_bytes is not None:
        sets.append("size_bytes = ?")
        params.append(size_bytes)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    params.append(download_id)
    conn.execute(f"UPDATE downloads SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def list_active(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return queued + in-flight downloads as raw rows (no Pydantic model
    for this entity yet — see phase2)."""
    placeholders = ", ".join("?" for _ in _ACTIVE)
    rows = conn.execute(
        f"SELECT * FROM downloads WHERE status IN ({placeholders}) ORDER BY id",
        _ACTIVE,
    ).fetchall()
    return list(rows)
