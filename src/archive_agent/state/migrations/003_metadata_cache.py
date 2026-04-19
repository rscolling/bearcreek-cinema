"""Migration 003: TMDb response cache.

One row per distinct API call. ``cache_key`` uses stable strings like
``"search:movie:<lowered title>:<year>"``, ``"id:movie:<tmdb_id>"``,
``"configuration"``, ``"genres:movie"``. Values that exceeded TTL stay
in the table until the next access evicts them (or until a future
janitor job sweeps them; not needed at this scale).
"""

from __future__ import annotations

import sqlite3

VERSION = 3
NAME = "metadata_cache"


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE metadata_cache (
            cache_key TEXT PRIMARY KEY,
            body_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX idx_metadata_cache_expires ON metadata_cache(expires_at);
        """
    )
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_metadata_cache_expires")
    conn.execute("DROP TABLE IF EXISTS metadata_cache")
    conn.commit()
