"""Migration 005: audit log of ``archive-agent recommend`` outputs.

Every run inserts a batch of rows (one per pick) tagged with the same
``batch_id``. This doubles as:

- The ``exclude_window_days`` mechanism — recent ``archive_id`` rows
  stop the ranker from recycling them.
- A cheap debug trail ("what was recommended last night and why?").
"""

from __future__ import annotations

import sqlite3

VERSION = 5
NAME = "ranked_candidates"


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE ranked_candidates (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id          TEXT NOT NULL,
            archive_id        TEXT NOT NULL,
            rank              INTEGER NOT NULL,
            score             REAL NOT NULL,
            reasoning         TEXT NOT NULL,
            provider          TEXT NOT NULL,
            profile_version   INTEGER NOT NULL,
            created_at        TEXT NOT NULL
        );
        CREATE INDEX idx_ranked_candidates_batch
            ON ranked_candidates(batch_id);
        CREATE INDEX idx_ranked_candidates_archive
            ON ranked_candidates(archive_id);
        CREATE INDEX idx_ranked_candidates_created
            ON ranked_candidates(created_at);
        """
    )
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_ranked_candidates_batch")
    conn.execute("DROP INDEX IF EXISTS idx_ranked_candidates_archive")
    conn.execute("DROP INDEX IF EXISTS idx_ranked_candidates_created")
    conn.execute("DROP TABLE IF EXISTS ranked_candidates")
    conn.commit()
