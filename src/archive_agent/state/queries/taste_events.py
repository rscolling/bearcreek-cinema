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


_RATING_KINDS_SQL = "('rated_down','rated_up','rated_love')"


def latest_rating_for_show(
    conn: sqlite3.Connection, show_id: str
) -> TasteEvent | None:
    """Newest ``roku_api``-sourced rating event for this show, or None.

    ADR-013 latest-wins semantics: the history is append-only, but the
    current thumb is whichever row has the greatest ``timestamp``.
    """
    row = conn.execute(
        f"""
        SELECT * FROM taste_events
            WHERE show_id = ?
              AND source = 'roku_api'
              AND kind IN {_RATING_KINDS_SQL}
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
        """,
        (show_id,),
    ).fetchone()
    return _row_to_event(row) if row is not None else None


def latest_rating_per_show(
    conn: sqlite3.Connection,
) -> dict[str, TasteEvent]:
    """Bulk read: ``{show_id: latest_rating_event}``.

    One row per show; avoids N+1 queries when the ranker or profile
    updater needs every show's current rating.
    """
    rows = conn.execute(
        f"""
        SELECT t.* FROM taste_events t
        JOIN (
            SELECT show_id, MAX(timestamp) AS max_ts
            FROM taste_events
            WHERE source = 'roku_api' AND kind IN {_RATING_KINDS_SQL}
              AND show_id IS NOT NULL
            GROUP BY show_id
        ) latest
            ON latest.show_id = t.show_id
           AND latest.max_ts = t.timestamp
        WHERE t.source = 'roku_api' AND t.kind IN {_RATING_KINDS_SQL}
        """
    ).fetchall()
    # Inner join can return multiple rows with the same (show_id, timestamp)
    # if a user rated twice in the same millisecond. Deterministic tiebreak:
    # highest id (newest insert) wins.
    by_show: dict[str, TasteEvent] = {}
    for row in rows:
        event = _row_to_event(row)
        assert event.show_id is not None  # SQL guarantees it
        existing = by_show.get(event.show_id)
        if existing is None or (event.id or 0) > (existing.id or 0):
            by_show[event.show_id] = event
    return by_show
