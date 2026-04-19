"""CRUD for raw episode playback events. These feed the show-state
aggregator only — never the taste profile directly."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from archive_agent.state.models import EpisodeWatch


def _row_to_watch(row: sqlite3.Row) -> EpisodeWatch:
    return EpisodeWatch(
        id=row["id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        show_id=row["show_id"],
        season=row["season"],
        episode=row["episode"],
        completion_pct=row["completion_pct"],
        jellyfin_item_id=row["jellyfin_item_id"],
    )


def insert_watch(conn: sqlite3.Connection, watch: EpisodeWatch) -> int:
    cur = conn.execute(
        """
        INSERT INTO episode_watches (
            timestamp, show_id, season, episode, completion_pct, jellyfin_item_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            watch.timestamp.isoformat(),
            watch.show_id,
            watch.season,
            watch.episode,
            watch.completion_pct,
            watch.jellyfin_item_id,
        ),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT produced no lastrowid — schema drift?")
    return int(rowid)


def list_for_show(conn: sqlite3.Connection, show_id: str) -> list[EpisodeWatch]:
    rows = conn.execute(
        "SELECT * FROM episode_watches WHERE show_id = ? ORDER BY timestamp",
        (show_id,),
    ).fetchall()
    return [_row_to_watch(r) for r in rows]
