"""Write helpers for the ``librarian_actions`` audit table.

Every destructive-or-notable librarian operation (download placement,
sampler promotion, eviction, skipped action) lands one row here.
Other phases call this function rather than issuing raw SQL (the
module-boundary rule in GUARDRAILS.md).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from archive_agent.librarian.zones import Zone

__all__ = ["LibrarianAction", "log_action"]

LibrarianAction = Literal["download", "promote", "evict", "skip"]


def log_action(
    conn: sqlite3.Connection,
    *,
    action: LibrarianAction,
    zone: Zone,
    reason: str,
    archive_id: str | None = None,
    show_id: str | None = None,
    size_bytes: int | None = None,
) -> int:
    """Insert a librarian_actions row, return its autoincrement id."""
    cur = conn.execute(
        """
        INSERT INTO librarian_actions (
            timestamp, action, zone, archive_id, show_id, size_bytes, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(),
            action,
            zone.value,
            archive_id,
            show_id,
            size_bytes,
            reason,
        ),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT produced no lastrowid — schema drift?")
    return int(rowid)
