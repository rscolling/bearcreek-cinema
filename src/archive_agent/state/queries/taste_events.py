"""CRUD for the ``taste_events`` table (movies + show-level binge events)."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from archive_agent.state.models import ContentType, TasteEvent, TasteEventKind


def _row_to_event(row: sqlite3.Row) -> TasteEvent:
    return TasteEvent(
        id=row["id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        content_type=ContentType(row["content_type"]),
        archive_id=row["archive_id"],
        show_id=row["show_id"],
        kind=TasteEventKind(row["kind"]),
        strength=row["strength"],
        source=row["source"],
    )


def insert_event(conn: sqlite3.Connection, event: TasteEvent) -> int:
    """Insert a taste event and return its autoincrement id."""
    cur = conn.execute(
        """
        INSERT INTO taste_events (
            timestamp, content_type, archive_id, show_id, kind, strength, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.timestamp.isoformat(),
            event.content_type.value,
            event.archive_id,
            event.show_id,
            event.kind.value,
            event.strength,
            event.source,
        ),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT produced no lastrowid — schema drift?")
    return int(rowid)


def list_since(conn: sqlite3.Connection, since: datetime) -> list[TasteEvent]:
    rows = conn.execute(
        "SELECT * FROM taste_events WHERE timestamp >= ? ORDER BY timestamp",
        (since.isoformat(),),
    ).fetchall()
    return [_row_to_event(r) for r in rows]
