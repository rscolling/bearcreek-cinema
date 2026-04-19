"""Shared fixtures for librarian/ tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from archive_agent.config import (
    ApiConfig,
    ArchiveConfig,
    Config,
    JellyfinConfig,
    LibrarianConfig,
    LlmClaudeConfig,
    LlmConfig,
    LlmOllamaConfig,
    LlmWorkflowsConfig,
    LoggingConfig,
    PathsConfig,
    TmdbConfig,
)
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def zone_tree(tmp_path: Path) -> Path:
    """A tmp_path with the four zones pre-created (empty)."""
    for name in ("movies", "tv", "recommendations", "tv-sampler"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def config(zone_tree: Path) -> Config:
    return Config(
        paths=PathsConfig(
            state_db=zone_tree / "state.db",
            media_movies=zone_tree / "movies",
            media_tv=zone_tree / "tv",
            media_recommendations=zone_tree / "recommendations",
            media_tv_sampler=zone_tree / "tv-sampler",
        ),
        jellyfin=JellyfinConfig(
            url="http://localhost:8096",
            api_key=SecretStr("k"),
            user_id="u",
        ),
        archive=ArchiveConfig(),
        tmdb=TmdbConfig(api_key=SecretStr("t")),
        llm=LlmConfig(
            workflows=LlmWorkflowsConfig(),
            ollama=LlmOllamaConfig(),
            claude=LlmClaudeConfig(),
        ),
        librarian=LibrarianConfig(max_disk_gb=10),  # small so we can exercise over-budget
        api=ApiConfig(),
        logging=LoggingConfig(),
    )


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()
