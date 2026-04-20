"""Shared fixtures for api/ tests.

``client`` is a ``TestClient`` with the lifespan active — routes see a
real (in-memory) state DB. ``app_no_lifespan`` returns the raw FastAPI
app for tests that want to override dependencies without running the
lifespan's DB + provider setup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from archive_agent.api.app import create_app
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
from archive_agent.logging import configure_logging


def _minimal_config(tmp: Path) -> Config:
    return Config(
        paths=PathsConfig(
            state_db=tmp / "state.db",
            media_movies=tmp / "movies",
            media_tv=tmp / "tv",
            media_recommendations=tmp / "rec",
            media_tv_sampler=tmp / "sampler",
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
        api=ApiConfig(host="127.0.0.1", port=8788),
        logging=LoggingConfig(),
    )


@pytest.fixture(autouse=True)
def _configure_logging() -> None:
    """Route structlog through stdlib logging so caplog sees events."""
    configure_logging(level="INFO", fmt="console")


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return _minimal_config(tmp_path)


@pytest.fixture
def app(config: Config) -> FastAPI:
    return create_app(config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with the real lifespan active.

    ``with TestClient(app)`` triggers the lifespan so routes see the
    DB + provider on ``app.state``.
    """
    with TestClient(app) as c:
        yield c
