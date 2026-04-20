"""Shared fixtures for commands/ tests."""

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
    RecommendConfig,
    TasteConfig,
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
            api_key=SecretStr("k"),
            user_id="u",
        ),
        archive=ArchiveConfig(),
        tmdb=TmdbConfig(api_key=SecretStr("t")),
        llm=LlmConfig(
            workflows=LlmWorkflowsConfig(nightly_ranking="ollama"),
            ollama=LlmOllamaConfig(),
            claude=LlmClaudeConfig(),
        ),
        librarian=LibrarianConfig(),
        taste=TasteConfig(),
        recommend=RecommendConfig(),
        api=ApiConfig(),
        logging=LoggingConfig(),
    )
