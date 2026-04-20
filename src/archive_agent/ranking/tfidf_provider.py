"""TF-IDF fallback LLMProvider — no LLM at all.

Used when Ollama is down or as a deliberate "cheap path" selected in
``[llm.workflows]``. Routing through ``audit_llm_call`` (with
``provider="tfidf"``) lets us compare fallback vs LLM usage from the
``llm_calls`` table later.

Only ``health_check`` is meaningful at phase1-05 time; the prefilter
itself arrives in phase3-02 / phase3-06.
"""

from __future__ import annotations

import sqlite3

from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

__all__ = ["TFIDFProvider"]

_MODEL_NAME = "tfidf-v1"


class TFIDFProvider:
    name = "tfidf"

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn

    async def health_check(self) -> HealthStatus:
        """Always ok — TF-IDF has no external dependency."""
        async with audit_llm_call("tfidf", _MODEL_NAME, "health_check", conn=self._conn) as ctx:
            return HealthStatus(
                status="ok",
                detail="no external dependency",
                model=_MODEL_NAME,
                latency_ms=ctx.latency_ms,
            )

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        raise NotImplementedError("TFIDFProvider.rank arrives in phase3-06")

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        raise NotImplementedError(
            "TFIDFProvider does not update the prose profile; use ollama or claude"
        )

    async def parse_search(self, query: str) -> SearchFilter:
        raise NotImplementedError("TFIDFProvider.parse_search arrives in phase4")
