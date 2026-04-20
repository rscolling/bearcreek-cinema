"""Migration 006: FTS5 virtual table over candidates (ADR-012).

Trigram tokenizer gives typo tolerance without any fuzzy-match
post-processing — "thrid man" still finds "The Third Man". Triggers
keep the index in sync with inserts, updates, and deletes. We only
index columns that matter for search (``title``, ``description``); the
FTS row is joined back to ``candidates`` for filtering + full-row
rehydration.

Requires SQLite 3.34+ for the ``trigram`` tokenizer.
"""

from __future__ import annotations

import sqlite3

VERSION = 6
NAME = "fts5_candidates"


def up(conn: sqlite3.Connection) -> None:
    if sqlite3.sqlite_version_info < (3, 34, 0):
        raise RuntimeError(
            f"FTS5 trigram tokenizer requires SQLite 3.34+, got {sqlite3.sqlite_version}"
        )
    conn.executescript(
        """
        CREATE VIRTUAL TABLE candidates_fts USING fts5(
            archive_id UNINDEXED,
            title,
            description,
            tokenize = 'trigram remove_diacritics 1'
        );

        -- Backfill from existing rows (no-op on a fresh install).
        INSERT INTO candidates_fts (archive_id, title, description)
            SELECT archive_id, title, description FROM candidates;

        CREATE TRIGGER candidates_ai AFTER INSERT ON candidates BEGIN
            INSERT INTO candidates_fts (archive_id, title, description)
                VALUES (NEW.archive_id, NEW.title, NEW.description);
        END;

        CREATE TRIGGER candidates_ad AFTER DELETE ON candidates BEGIN
            DELETE FROM candidates_fts WHERE archive_id = OLD.archive_id;
        END;

        -- Single trigger covers multi-column UPDATEs: we rebuild the
        -- FTS row wholesale rather than running per-column triggers
        -- that would fire twice on ``UPDATE candidates SET title=?,
        -- description=?``.
        CREATE TRIGGER candidates_au AFTER UPDATE ON candidates BEGIN
            DELETE FROM candidates_fts WHERE archive_id = OLD.archive_id;
            INSERT INTO candidates_fts (archive_id, title, description)
                VALUES (NEW.archive_id, NEW.title, NEW.description);
        END;
        """
    )
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS candidates_ai;
        DROP TRIGGER IF EXISTS candidates_ad;
        DROP TRIGGER IF EXISTS candidates_au;
        DROP TABLE IF EXISTS candidates_fts;
        """
    )
    conn.commit()
