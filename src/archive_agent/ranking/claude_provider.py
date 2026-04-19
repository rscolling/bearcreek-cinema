"""Anthropic Claude LLMProvider (optional, cloud).

Only active when a workflow in ``[llm.workflows]`` names ``"claude"``.
If the API key is missing, ``health_check`` returns status=down with a
clear message and writes **no** ``llm_calls`` row — we never silently
fall through to Claude, and an unused-but-configured provider shouldn't
pollute the audit log.
"""

from __future__ import annotations

import sqlite3

import anthropic

from archive_agent.config import LlmClaudeConfig
from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

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

    async def health_check(self) -> HealthStatus:
        if self._config.api_key is None:
            return HealthStatus(
                status="down",
                detail="ANTHROPIC_API_KEY not set; ClaudeProvider is disabled",
                model=self._config.model,
            )
        async with audit_llm_call(
            "claude", self._config.model, "health_check", conn=self._conn
        ) as ctx:
            try:
                client = anthropic.AsyncAnthropic(api_key=self._config.api_key.get_secret_value())
                resp = await client.messages.create(
                    model=self._config.model,
                    max_tokens=16,
                    messages=[{"role": "user", "content": "Respond with exactly: OK"}],
                )
                text = getattr(resp.content[0], "text", "") if resp.content else ""
                if "OK" not in text:
                    ctx.outcome = "malformed"
                    return HealthStatus(
                        status="degraded",
                        detail=f"unexpected reply: {text!r}",
                        model=self._config.model,
                        latency_ms=ctx.latency_ms,
                    )
                return HealthStatus(
                    status="ok",
                    detail="smoke round-trip passed",
                    model=self._config.model,
                    latency_ms=ctx.latency_ms,
                )
            except Exception as exc:
                ctx.outcome = "error"
                return HealthStatus(
                    status="down",
                    detail=f"{type(exc).__name__}: {exc}",
                    model=self._config.model,
                    latency_ms=ctx.latency_ms,
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
