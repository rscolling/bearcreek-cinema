"""Shared fixtures for taste/ tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from archive_agent.config import TasteConfig
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


@pytest.fixture
def taste_config() -> TasteConfig:
    return TasteConfig()
