"""Tiny SQLite cache for TMDb responses.

Every API call goes through ``get`` → miss → real fetch → ``put``. The
TTL is per-caller (searches 14d, by-id 30d, configuration 24h, genre
lists 30d). Expired entries stay in the table but are treated as
misses; they get overwritten on the next fetch.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = ["get", "put"]


def get(
    conn: sqlite3.Connection,
    cache_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the cached body (parsed JSON dict) or ``None`` on
    miss / expired."""
    now = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT body_json, expires_at FROM metadata_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < now:
        return None
    parsed: dict[str, Any] = json.loads(row["body_json"])
    return parsed


def put(
    conn: sqlite3.Connection,
    cache_key: str,
    body: dict[str, Any],
    ttl: timedelta,
    *,
    now: datetime | None = None,
) -> None:
    """INSERT OR REPLACE the entry. ``ttl`` is added to ``now`` for
    ``expires_at``."""
    now = now or datetime.now(UTC)
    conn.execute(
        """
        INSERT INTO metadata_cache (cache_key, body_json, fetched_at, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            body_json = excluded.body_json,
            fetched_at = excluded.fetched_at,
            expires_at = excluded.expires_at
        """,
        (cache_key, json.dumps(body), now.isoformat(), (now + ttl).isoformat()),
    )
    conn.commit()
