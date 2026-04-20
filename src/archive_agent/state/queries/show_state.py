"""CRUD for per-show aggregator state."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from archive_agent.state.models import ShowState, TasteEventKind


def _row_to_state(row: sqlite3.Row) -> ShowState:
    return ShowState(
        show_id=row["show_id"],
        episodes_finished=row["episodes_finished"],
        episodes_abandoned=row["episodes_abandoned"],
        episodes_available=row["episodes_available"],
        last_playback_at=datetime.fromisoformat(row["last_playback_at"])
        if row["last_playback_at"]
        else None,
        started_at=datetime.fromisoformat(row["started_at"]),
        last_emitted_event=TasteEventKind(row["last_emitted_event"])
        if row["last_emitted_event"]
        else None,
        last_emitted_at=datetime.fromisoformat(row["last_emitted_at"])
        if row["last_emitted_at"]
        else None,
    )


def upsert(conn: sqlite3.Connection, state: ShowState) -> None:
    conn.execute(
        """
        INSERT INTO show_state (
            show_id, episodes_finished, episodes_abandoned, episodes_available,
            last_playback_at, started_at, last_emitted_event, last_emitted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(show_id) DO UPDATE SET
            episodes_finished=excluded.episodes_finished,
            episodes_abandoned=excluded.episodes_abandoned,
            episodes_available=excluded.episodes_available,
            last_playback_at=excluded.last_playback_at,
            last_emitted_event=excluded.last_emitted_event,
            last_emitted_at=excluded.last_emitted_at
        """,
        (
            state.show_id,
            state.episodes_finished,
            state.episodes_abandoned,
            state.episodes_available,
            state.last_playback_at.isoformat() if state.last_playback_at else None,
            state.started_at.isoformat(),
            state.last_emitted_event.value if state.last_emitted_event else None,
            state.last_emitted_at.isoformat() if state.last_emitted_at else None,
        ),
    )
    conn.commit()


def get(conn: sqlite3.Connection, show_id: str) -> ShowState | None:
    row = conn.execute("SELECT * FROM show_state WHERE show_id = ?", (show_id,)).fetchone()
    return _row_to_state(row) if row is not None else None


def list_all_active(conn: sqlite3.Connection, *, since: datetime) -> list[ShowState]:
    """Return shows with any playback activity on or after ``since``.

    ``since`` is inclusive. A show with ``last_playback_at IS NULL`` is
    never "active" by this definition.
    """
    rows = conn.execute(
        """
        SELECT * FROM show_state
        WHERE last_playback_at IS NOT NULL AND last_playback_at >= ?
        ORDER BY last_playback_at DESC
        """,
        (since.isoformat(),),
    ).fetchall()
    return [_row_to_state(r) for r in rows]


def list_all(conn: sqlite3.Connection) -> list[ShowState]:
    """Return every row in ``show_state`` in deterministic order.

    Used by the aggregator: it must consider shows that have gone
    quiet (no playback in a long time) because that's the trigger for
    ``BINGE_NEGATIVE``.
    """
    rows = conn.execute("SELECT * FROM show_state ORDER BY show_id").fetchall()
    return [_row_to_state(r) for r in rows]


def list_show_ids_with_episodes(conn: sqlite3.Connection) -> list[str]:
    """Return every distinct ``show_id`` that has at least one
    episode candidate or one episode watch. Used to seed new
    ``show_state`` rows when the aggregator encounters a show that
    has playback but no state row yet.
    """
    rows = conn.execute(
        """
        SELECT show_id FROM candidates
            WHERE content_type = 'episode' AND show_id IS NOT NULL
        UNION
        SELECT show_id FROM episode_watches
            WHERE show_id IS NOT NULL
        """
    ).fetchall()
    return sorted({row["show_id"] for row in rows})
