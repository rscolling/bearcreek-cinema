"""Shared fixtures for state/ tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory DB with all migrations applied."""
    conn = connect(":memory:")
    applied = apply_pending(conn)
    assert applied and applied[0] == 1, f"expected migrations starting at 1, got {applied}"
    yield conn
    conn.close()
