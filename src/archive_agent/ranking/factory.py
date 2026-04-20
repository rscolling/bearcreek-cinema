"""LLMProvider construction.

Three entry points:

- ``make_provider("ollama"|"claude"|"tfidf", config)`` — explicit,
  used by the CLI (``archive-agent health <name>``) and by tests.
- ``make_provider_for_workflow("nightly_ranking", config)`` — reads
  ``[llm.workflows]`` and picks the provider configured for that
  workflow. Used by the daemon loop.
- ``make_fallback_provider(workflow, config)`` — wraps the workflow
  provider with ``FallbackProvider`` so failures degrade to TF-IDF
  instead of raising. Per ADR-002, **only** TF-IDF is a legal
  fallback target — never another LLM.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.ranking.claude_provider import ClaudeProvider
from archive_agent.ranking.ollama_provider import OllamaProvider
from archive_agent.ranking.provider import HealthStatus, LLMProvider
from archive_agent.ranking.tfidf_provider import TFIDFProvider
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

__all__ = [
    "FallbackProvider",
    "make_fallback_provider",
    "make_provider",
    "make_provider_for_workflow",
]

ProviderName = Literal["ollama", "claude", "tfidf"]
Workflow = Literal["nightly_ranking", "profile_update", "nl_search"]

_log = get_logger("archive_agent.ranking.factory")


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


def make_fallback_provider(
    workflow: Workflow,
    config: Config,
    *,
    conn: sqlite3.Connection | None = None,
) -> LLMProvider:
    """Build a ``FallbackProvider`` chain for a workflow.

    Primary is the workflow-configured provider; secondary is always
    ``TFIDFProvider``. When primary == TFIDF (already), the chain is
    a single-element no-op wrapper — still callable, just with no
    fallback to exercise.
    """
    primary = make_provider_for_workflow(workflow, config, conn=conn)
    if primary.name == "tfidf":
        return primary
    tfidf = TFIDFProvider(conn=conn)
    return FallbackProvider(primary=primary, fallback=tfidf)


class FallbackProvider:
    """Composite ``LLMProvider`` that degrades primary to fallback on error.

    Invariants (ADR-002):

    - Only ``tfidf`` is ever a legal fallback target.
    - Every method forwards first to ``primary``; any exception is
      caught and fed into ``fallback`` with a structlog
      ``provider_fallback`` event.
    - An empty but non-erroring primary reply is considered valid —
      we don't fall back just because the model returned nothing.
    """

    def __init__(self, *, primary: LLMProvider, fallback: LLMProvider) -> None:
        if fallback.name != "tfidf":
            raise ValueError(f"FallbackProvider requires tfidf as fallback, got {fallback.name!r}")
        self._primary = primary
        self._fallback = fallback
        self.name = primary.name  # Surface the primary to audit rows.

    async def health_check(self) -> HealthStatus:
        try:
            return await self._primary.health_check()
        except Exception as exc:
            _log_fallback("health_check", self._primary.name, str(exc))
            return await self._fallback.health_check()

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        try:
            return await self._primary.rank(profile, candidates, n, ratings=ratings)
        except Exception as exc:
            _log_fallback("rank", self._primary.name, str(exc))
            return await self._fallback.rank(profile, candidates, n, ratings=ratings)

    async def update_profile(self, current: TasteProfile, events: list[TasteEvent]) -> TasteProfile:
        try:
            return await self._primary.update_profile(current, events)
        except Exception as exc:
            _log_fallback("update_profile", self._primary.name, str(exc))
            return await self._fallback.update_profile(current, events)

    async def parse_search(self, query: str) -> SearchFilter:
        try:
            return await self._primary.parse_search(query)
        except Exception as exc:
            _log_fallback("parse_search", self._primary.name, str(exc))
            return await self._fallback.parse_search(query)


def _log_fallback(workflow: str, from_provider: str, reason: str) -> None:
    _log.warning(
        "provider_fallback",
        workflow=workflow,
        **{"from": from_provider, "to": "tfidf"},
        reason=reason,
    )
