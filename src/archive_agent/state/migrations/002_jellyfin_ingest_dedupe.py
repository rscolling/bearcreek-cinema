"""Migration 002: unique index to make Jellyfin history ingestion idempotent.

``episode_watches`` picks up ``INSERT OR IGNORE`` semantics via the unique
index; re-running ``history sync`` against an unchanged Jellyfin state is
a no-op on disk.
"""

from __future__ import annotations

import sqlite3

VERSION = 2
NAME = "jellyfin_ingest_dedupe"


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX idx_episode_watches_dedupe "
        "ON episode_watches(jellyfin_item_id, timestamp)"
    )
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_episode_watches_dedupe")
    conn.commit()
