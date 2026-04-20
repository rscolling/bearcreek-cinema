"""CRUD round-trip for the ``taste_profile_versions`` table."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from archive_agent.state.models import TasteProfile
from archive_agent.state.queries import taste_profile_versions as q


def _profile(version: int, summary: str = "") -> TasteProfile:
    return TasteProfile(version=version, updated_at=datetime.now(UTC), summary=summary)


def test_empty_returns_none(db: sqlite3.Connection) -> None:
    assert q.get_latest_profile(db) is None


def test_insert_assigns_sequential_versions(db: sqlite3.Connection) -> None:
    v1 = q.insert_profile(db, _profile(42, "first"))  # requested version ignored
    v2 = q.insert_profile(db, _profile(42, "second"))

    assert v1 == 1
    assert v2 == 2

    latest = q.get_latest_profile(db)
    assert latest is not None
    assert latest.version == 2
    assert latest.summary == "second"


def test_list_versions_returns_most_recent_first(db: sqlite3.Connection) -> None:
    q.insert_profile(db, _profile(0, "v1"))
    q.insert_profile(db, _profile(0, "v2"))
    q.insert_profile(db, _profile(0, "v3"))

    versions = q.list_versions(db, limit=5)

    assert [v for v, _, _ in versions] == [3, 2, 1]
    assert [s for _, _, s in versions] == ["v3", "v2", "v1"]


def test_list_versions_honors_limit(db: sqlite3.Connection) -> None:
    for i in range(5):
        q.insert_profile(db, _profile(0, f"v{i}"))

    versions = q.list_versions(db, limit=2)

    assert len(versions) == 2
