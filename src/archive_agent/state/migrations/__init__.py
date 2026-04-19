"""Migration runner.

Each migration is a file in this directory named ``NNN_<slug>.py``
exporting ``VERSION: int``, ``NAME: str``, ``up(conn)`` and
``down(conn)``. Filenames start with a digit so they aren't valid Python
module names — we load them by path with ``importlib.util`` instead of
``import archive_agent.state.migrations.001_initial``.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

__all__ = [
    "apply_pending",
    "apply_version",
    "current_version",
    "discover",
    "pending_versions",
    "revert_version",
]


def _schema_version_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return row is not None


def current_version(conn: sqlite3.Connection) -> int:
    """Return the latest applied migration version, or 0 if none."""
    if not _schema_version_table_exists(conn):
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def discover() -> list[ModuleType]:
    """Load all migration modules in this directory, sorted by version."""
    mig_dir = Path(__file__).parent
    modules: list[ModuleType] = []
    for path in sorted(mig_dir.glob("[0-9][0-9][0-9]_*.py")):
        spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load migration at {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "VERSION") or not hasattr(mod, "up") or not hasattr(mod, "down"):
            raise RuntimeError(f"migration {path.name} missing VERSION/up/down")
        modules.append(mod)
    return sorted(modules, key=lambda m: int(m.VERSION))


def pending_versions(conn: sqlite3.Connection) -> list[int]:
    cur = current_version(conn)
    return [int(m.VERSION) for m in discover() if int(m.VERSION) > cur]


def apply_pending(conn: sqlite3.Connection) -> list[int]:
    """Run every migration newer than ``current_version`` in order.

    Each migration's ``up(conn)`` runs inside a single transaction. If
    it raises, the transaction rolls back and no schema_version row is
    written.
    """
    applied: list[int] = []
    cur = current_version(conn)
    for mod in discover():
        version = int(mod.VERSION)
        if version <= cur:
            continue
        _record_apply(conn, mod)
        applied.append(version)
    return applied


def apply_version(conn: sqlite3.Connection, version: int) -> None:
    """Apply exactly one migration by version number (useful for tests)."""
    for mod in discover():
        if int(mod.VERSION) == version:
            _record_apply(conn, mod)
            return
    raise ValueError(f"no migration with VERSION={version}")


def revert_version(conn: sqlite3.Connection, version: int) -> None:
    """Revert exactly one migration by version number (tests only)."""
    for mod in discover():
        if int(mod.VERSION) == version:
            mod.down(conn)
            if _schema_version_table_exists(conn):
                conn.execute("DELETE FROM schema_version WHERE version = ?", (version,))
                conn.commit()
            return
    raise ValueError(f"no migration with VERSION={version}")


def _record_apply(conn: sqlite3.Connection, mod: ModuleType) -> None:
    try:
        mod.up(conn)
    except Exception:
        conn.rollback()
        raise
    # schema_version table is created by 001_initial itself — safe to
    # INSERT after up() returns.
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (int(mod.VERSION), datetime.now(UTC).isoformat()),
    )
    conn.commit()
