"""Append-only reads/writes for ``taste_profile_versions``.

Every profile update creates a new row (monotonic ``version``). The
"current" profile is whichever row has the largest version. History is
kept for audit and roll-back diagnostics — never pruned.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from archive_agent.state.models import TasteProfile


def get_latest_profile(conn: sqlite3.Connection) -> TasteProfile | None:
    """Return the newest ``TasteProfile``, or None if the table is empty."""
    row = conn.execute(
        "SELECT profile_json FROM taste_profile_versions "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return TasteProfile.model_validate_json(row["profile_json"])


def insert_profile(conn: sqlite3.Connection, profile: TasteProfile) -> int:
    """Insert a new row, assigning ``version = MAX(existing) + 1``.

    The caller's ``profile.version`` is overwritten to match — this is
    the one place versions are assigned, so there's no way to land a
    gap or a duplicate.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM taste_profile_versions"
    ).fetchone()
    next_version = int(row["v"]) + 1
    stored = profile.model_copy(update={"version": next_version})
    conn.execute(
        "INSERT INTO taste_profile_versions (version, updated_at, profile_json) "
        "VALUES (?, ?, ?)",
        (next_version, stored.updated_at.isoformat(), stored.model_dump_json()),
    )
    conn.commit()
    return next_version


def list_versions(
    conn: sqlite3.Connection, *, limit: int = 10
) -> list[tuple[int, datetime, str]]:
    """Return ``[(version, updated_at, summary_snippet)]`` most-recent first."""
    rows = conn.execute(
        "SELECT version, updated_at, profile_json FROM taste_profile_versions "
        "ORDER BY version DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out: list[tuple[int, datetime, str]] = []
    for row in rows:
        profile = TasteProfile.model_validate_json(row["profile_json"])
        snippet = profile.summary[:120].replace("\n", " ")
        out.append((int(row["version"]), datetime.fromisoformat(row["updated_at"]), snippet))
    return out


__all__ = ["get_latest_profile", "insert_profile", "list_versions"]
