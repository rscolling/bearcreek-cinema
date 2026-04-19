"""Migration runner: discover, apply, revert, re-apply."""

from __future__ import annotations

import sqlite3

from archive_agent.state.db import connect
from archive_agent.state.migrations import (
    apply_pending,
    current_version,
    discover,
    pending_versions,
    revert_version,
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def test_discover_finds_initial() -> None:
    mods = discover()
    versions = [int(m.VERSION) for m in mods]
    assert 1 in versions
    assert versions == sorted(versions)


def test_current_version_fresh_is_zero() -> None:
    conn = connect(":memory:")
    try:
        assert current_version(conn) == 0
    finally:
        conn.close()


def test_pending_reflects_current_version() -> None:
    conn = connect(":memory:")
    try:
        expected_all = [int(m.VERSION) for m in discover()]
        assert pending_versions(conn) == expected_all
        apply_pending(conn)
        assert pending_versions(conn) == []
    finally:
        conn.close()


def test_apply_pending_creates_all_tables() -> None:
    conn = connect(":memory:")
    try:
        expected_all = [int(m.VERSION) for m in discover()]
        assert apply_pending(conn) == expected_all
        tables = _tables(conn)
        expected = {
            "candidates",
            "taste_events",
            "episode_watches",
            "show_state",
            "taste_profile_versions",
            "downloads",
            "librarian_actions",
            "llm_calls",
            "schema_version",
        }
        assert expected <= tables
        assert current_version(conn) == max(expected_all)
    finally:
        conn.close()


def test_apply_pending_is_idempotent() -> None:
    conn = connect(":memory:")
    try:
        first = apply_pending(conn)
        second = apply_pending(conn)
        assert first == [int(m.VERSION) for m in discover()]
        assert second == []
    finally:
        conn.close()


def test_down_then_up_round_trip_for_initial() -> None:
    conn = connect(":memory:")
    try:
        apply_pending(conn)
        assert "candidates" in _tables(conn)
        # Revert later migrations in reverse order, then the initial one
        for mod in reversed(discover()):
            revert_version(conn, int(mod.VERSION))
        assert "candidates" not in _tables(conn)
        assert current_version(conn) == 0
        re_applied = apply_pending(conn)
        assert re_applied == [int(m.VERSION) for m in discover()]
        assert "candidates" in _tables(conn)
    finally:
        conn.close()
