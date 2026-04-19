"""Shared fixtures for ranking/ tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
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
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A minimally-valid Config that exercises every section."""
    return Config(
        paths=PathsConfig(
            state_db=tmp_path / "state.db",
            media_movies=tmp_path / "movies",
            media_tv=tmp_path / "tv",
            media_recommendations=tmp_path / "rec",
            media_tv_sampler=tmp_path / "sampler",
        ),
        jellyfin=JellyfinConfig(
            url="http://localhost:8096",
            api_key=SecretStr("jelly-key"),
            user_id="user-1",
        ),
        archive=ArchiveConfig(),
        tmdb=TmdbConfig(api_key=SecretStr("tmdb-key")),
        llm=LlmConfig(
            workflows=LlmWorkflowsConfig(nightly_ranking="ollama"),
            ollama=LlmOllamaConfig(host="http://localhost:11434"),
            claude=LlmClaudeConfig(),  # api_key=None — claude disabled
        ),
        librarian=LibrarianConfig(),
        api=ApiConfig(),
        logging=LoggingConfig(),
    )


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Some assertions are cleaner with a deterministic timestamp."""
    fixed = datetime(2026, 4, 19, 12, 0, 0)
    return fixed
