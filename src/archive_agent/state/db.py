"""SQLite connection management for the state DB.

Only this module opens connections. All other code goes through
``get_db()`` (for the singleton used by the daemon / CLI) or ``connect()``
(for tests and any place that needs its own connection, like :memory:).

Writes should come from one thread. sqlite3 is set to
``check_same_thread=False`` so we can share the connection across asyncio
tasks, but serialization is an application-layer concern — use a queue
if writes start coming from multiple async contexts.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

__all__ = ["close_db", "connect", "get_db", "init_db", "reset_cached_db"]


_IN_MEMORY = ":memory:"
_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a configured sqlite3 connection.

    Sets ``row_factory=sqlite3.Row``, enables foreign keys, and flips
    journal mode to WAL for on-disk DBs (:memory: stays the default).
    Callers own the lifecycle — ``close()`` when done.
    """
    path_str = str(path)
    if path_str != _IN_MEMORY:
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_str, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path_str != _IN_MEMORY:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    """Return the process-wide singleton connection, opening it on demand.

    The path comes from ``config.paths.state_db``. Import the config
    lazily so tests can swap the connection via ``reset_cached_db`` +
    ``connect(":memory:")`` without pulling in config machinery.
    """
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is None:
            from archive_agent.config import load_config

            cfg = load_config()
            _conn = connect(cfg.paths.state_db)
    return _conn


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def reset_cached_db() -> None:
    """Drop the cached singleton without closing it (for test fixtures)."""
    global _conn
    with _lock:
        _conn = None


def init_db(db_path: Path | str, *, dry_run: bool = False) -> list[int]:
    """Open the DB at ``db_path`` and apply pending migrations.

    Returns the list of migration versions applied in this call. When
    ``dry_run`` is True, returns the list that *would* be applied without
    touching the DB.
    """
    from archive_agent.state.migrations import apply_pending, pending_versions

    if dry_run:
        # Throwaway connection to read schema_version without persisting.
        # For a brand-new DB, schema_version won't exist yet — pending
        # treats that as "everything pending" via current_version=0.
        tmp = connect(db_path)
        try:
            return pending_versions(tmp)
        finally:
            tmp.close()
    conn = connect(db_path)
    try:
        return apply_pending(conn)
    finally:
        conn.close()
