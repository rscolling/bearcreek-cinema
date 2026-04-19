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
    assert applied == [1], f"expected [1] to be applied, got {applied}"
    yield conn
    conn.close()
