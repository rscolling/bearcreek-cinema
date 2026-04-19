"""Migration 001: initial schema. Runs the full schema.sql."""

from __future__ import annotations

import sqlite3
from pathlib import Path

VERSION = 1
NAME = "initial"

_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()


def down(conn: sqlite3.Connection) -> None:
    # Drops every table the initial schema created. No data preservation —
    # this is only for test round-trips and aborted dev installs.
    for table in (
        "candidates",
        "taste_events",
        "episode_watches",
        "show_state",
        "taste_profile_versions",
        "downloads",
        "librarian_actions",
        "llm_calls",
        "schema_version",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
