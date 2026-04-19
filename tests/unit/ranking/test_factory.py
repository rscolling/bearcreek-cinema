"""Factory dispatch and workflow routing."""

from __future__ import annotations

import pytest

from archive_agent.config import Config
from archive_agent.ranking import (
    ClaudeProvider,
    OllamaProvider,
    TFIDFProvider,
    make_provider,
    make_provider_for_workflow,
)


def test_make_provider_ollama(config: Config) -> None:
    p = make_provider("ollama", config)
    assert isinstance(p, OllamaProvider)
    assert p.name == "ollama"


def test_make_provider_claude(config: Config) -> None:
    p = make_provider("claude", config)
    assert isinstance(p, ClaudeProvider)


def test_make_provider_tfidf(config: Config) -> None:
    p = make_provider("tfidf", config)
    assert isinstance(p, TFIDFProvider)


def test_make_provider_unknown_name_raises(config: Config) -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider("mystery", config)  # type: ignore[arg-type]


def test_workflow_routes_to_configured_provider(config: Config) -> None:
    # config.llm.workflows.nightly_ranking defaults to "ollama" in the fixture
    p = make_provider_for_workflow("nightly_ranking", config)
    assert isinstance(p, OllamaProvider)
