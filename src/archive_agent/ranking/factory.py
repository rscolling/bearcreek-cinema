"""LLMProvider construction.

Two entry points:

- ``make_provider("ollama"|"claude"|"tfidf", config)`` — explicit,
  used by the CLI (``archive-agent health <name>``) and by tests.
- ``make_provider_for_workflow("nightly_ranking", config)`` — reads
  ``[llm.workflows]`` and picks the provider configured for that
  workflow. Used by the daemon loop.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from archive_agent.config import Config
from archive_agent.ranking.claude_provider import ClaudeProvider
from archive_agent.ranking.ollama_provider import OllamaProvider
from archive_agent.ranking.provider import LLMProvider
from archive_agent.ranking.tfidf_provider import TFIDFProvider

__all__ = ["make_provider", "make_provider_for_workflow"]

ProviderName = Literal["ollama", "claude", "tfidf"]
Workflow = Literal["nightly_ranking", "profile_update", "nl_search"]


def make_provider(
    name: ProviderName,
    config: Config,
    *,
    conn: sqlite3.Connection | None = None,
) -> LLMProvider:
    if name == "ollama":
        return OllamaProvider(config.llm.ollama, conn=conn)
    if name == "claude":
        return ClaudeProvider(config.llm.claude, conn=conn)
    if name == "tfidf":
        return TFIDFProvider(conn=conn)
    raise ValueError(f"unknown provider name: {name!r}")


def make_provider_for_workflow(
    workflow: Workflow,
    config: Config,
    *,
    conn: sqlite3.Connection | None = None,
) -> LLMProvider:
    selected: ProviderName = getattr(config.llm.workflows, workflow)
    return make_provider(selected, config, conn=conn)
