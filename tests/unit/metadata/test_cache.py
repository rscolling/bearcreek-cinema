"""metadata_cache CRUD + TTL semantics."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from archive_agent.metadata import cache


def test_put_then_get_roundtrips(db: sqlite3.Connection) -> None:
    cache.put(db, "k1", {"hello": "world"}, timedelta(hours=1))
    assert cache.get(db, "k1") == {"hello": "world"}


def test_miss_returns_none(db: sqlite3.Connection) -> None:
    assert cache.get(db, "never-written") is None


def test_expired_entry_is_a_miss(db: sqlite3.Connection) -> None:
    # Write with a negative TTL → already expired
    cache.put(db, "stale", {"x": 1}, timedelta(hours=-1))
    assert cache.get(db, "stale") is None


def test_get_honors_injected_now(db: sqlite3.Connection) -> None:
    base = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    cache.put(db, "k", {"x": 1}, timedelta(hours=1), now=base)
    # 30 minutes later — still fresh
    assert cache.get(db, "k", now=base + timedelta(minutes=30)) == {"x": 1}
    # 2 hours later — expired
    assert cache.get(db, "k", now=base + timedelta(hours=2)) is None


def test_put_overwrites_existing_key(db: sqlite3.Connection) -> None:
    cache.put(db, "k", {"v": 1}, timedelta(hours=1))
    cache.put(db, "k", {"v": 2}, timedelta(hours=1))
    assert cache.get(db, "k") == {"v": 2}


def test_one_row_per_key(db: sqlite3.Connection) -> None:
    for _ in range(5):
        cache.put(db, "k", {"v": 1}, timedelta(hours=1))
    count = db.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0]
    assert count == 1
