"""TF-IDF fallback LLMProvider — no LLM at all.

Used when Ollama is down or as a deliberate "cheap path" selected in
``[llm.workflows]``. The TF-IDF prefilter itself lives in ``ranking``
(phase3-02); this class just exposes it as a provider so the factory
can swap it in transparently.

Only ``health_check`` is meaningful at phase1-05 time — the rest raise
``NotImplementedError`` until the prefilter lands.
"""

from __future__ import annotations

import sqlite3
import time

from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.state.queries import llm_calls

__all__ = ["TFIDFProvider"]


class TFIDFProvider:
    name = "tfidf"

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn

    def _log(self, workflow: str, latency_ms: int, outcome: str = "ok") -> None:
        if self._conn is None:
            return
        llm_calls.insert(
            self._conn,
            provider="tfidf",
            model="tfidf",
            workflow=workflow,
            latency_ms=latency_ms,
            outcome=outcome,  # type: ignore[arg-type]
        )

    async def health_check(self) -> HealthStatus:
        """Always ok — TF-IDF has no external dependency."""
        t0 = time.perf_counter()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._log("health_check", latency_ms)
        return HealthStatus(
            status="ok",
            detail="no external dependency",
            model="tfidf",
            latency_ms=latency_ms,
        )

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
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
