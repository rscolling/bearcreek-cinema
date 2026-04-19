"""Anthropic Claude LLMProvider (optional, cloud).

Only active when a workflow in ``[llm.workflows]`` names ``"claude"``.
If the API key is missing, ``health_check`` returns status=down with a
clear message — we never silently fall through to Claude.
"""

from __future__ import annotations

import sqlite3
import time

import anthropic

from archive_agent.config import LlmClaudeConfig
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.state.queries import llm_calls

__all__ = ["ClaudeProvider"]


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        config: LlmClaudeConfig,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        self._config = config
        self._conn = conn

    def _log(
        self,
        workflow: str,
        latency_ms: int,
        outcome: str = "ok",
        model: str | None = None,
    ) -> None:
        if self._conn is None:
            return
        llm_calls.insert(
            self._conn,
            provider="claude",
            model=model or self._config.model,
            workflow=workflow,
            latency_ms=latency_ms,
            outcome=outcome,  # type: ignore[arg-type]
        )

    async def health_check(self) -> HealthStatus:
        if self._config.api_key is None:
            return HealthStatus(
                status="down",
                detail="ANTHROPIC_API_KEY not set; ClaudeProvider is disabled",
                model=self._config.model,
            )
        t0 = time.perf_counter()
        try:
            client = anthropic.AsyncAnthropic(api_key=self._config.api_key.get_secret_value())
            resp = await client.messages.create(
                model=self._config.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Respond with exactly: OK"}],
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            # Anthropic responses can contain blocks other than plain text
            # (thinking, tool-use, etc.); we only care about the TextBlock case.
            text = getattr(resp.content[0], "text", "") if resp.content else ""
            if "OK" not in text:
                self._log("health_check", latency_ms, outcome="malformed")
                return HealthStatus(
                    status="degraded",
                    detail=f"unexpected reply: {text!r}",
                    model=self._config.model,
                    latency_ms=latency_ms,
                )
            self._log("health_check", latency_ms, outcome="ok")
            return HealthStatus(
                status="ok",
                detail="smoke round-trip passed",
                model=self._config.model,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            self._log("health_check", latency_ms, outcome="error")
            return HealthStatus(
                status="down",
                detail=f"{type(exc).__name__}: {exc}",
                model=self._config.model,
                latency_ms=latency_ms,
            )

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
    ) -> list[RankedCandidate]:
        raise NotImplementedError("ClaudeProvider.rank arrives in phase3-07")

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        raise NotImplementedError("ClaudeProvider.update_profile arrives in phase3-07")

    async def parse_search(self, query: str) -> SearchFilter:
        raise NotImplementedError("ClaudeProvider.parse_search arrives in phase4")
