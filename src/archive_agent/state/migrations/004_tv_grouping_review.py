"""Migration 004: review queue for low-confidence TV episode groupings.

Items land here when ``classify_episode`` returns ``low`` or ``none``.
They sit unresolved until phase6 (or manual curation) walks the queue
and decides to force-assign or leave standalone.
"""

from __future__ import annotations

import sqlite3

VERSION = 4
NAME = "tv_grouping_review"


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE tv_grouping_review (
            archive_id TEXT PRIMARY KEY,
            confidence TEXT NOT NULL CHECK (confidence IN ('low', 'none')),
            reason TEXT NOT NULL,
            suggested_show_id TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT
        );
        CREATE INDEX idx_tv_grouping_review_unresolved
            ON tv_grouping_review(created_at)
            WHERE reviewed_at IS NULL;
        """
    )
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_tv_grouping_review_unresolved")
    conn.execute("DROP TABLE IF EXISTS tv_grouping_review")
    conn.commit()
