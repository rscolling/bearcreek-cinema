"""Local Ollama LLMProvider (default).

Uses two client libraries side by side:

- ``ollama.AsyncClient`` for model listing + lifecycle probes (native API).
- ``instructor.AsyncInstructor`` over the OpenAI-compatible ``/v1/...``
  endpoint for structured-JSON round trips. Instructor handles
  Pydantic response-model coercion and retries on malformed output.

Every call (including ``health_check``) writes one row to
``llm_calls`` through the optional sqlite3 connection; tests pass an
in-memory connection, production wires the singleton.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import instructor
import ollama
from pydantic import BaseModel

from archive_agent.config import LlmOllamaConfig
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.state.queries import llm_calls

__all__ = ["OllamaProvider"]


class _SmokeResponse(BaseModel):
    ok: bool
    model: str


_SMOKE_PROMPT = 'Return only this JSON and nothing else: {"ok": true, "model": "qwen2.5"}'


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        config: LlmOllamaConfig,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        self._config = config
        self._conn = conn
        self._host = config.host.rstrip("/")

    # --- client factories (lazy so construction stays fast) --------------

    def _native_client(self) -> ollama.AsyncClient:
        return ollama.AsyncClient(host=self._host)

    def _instructor_client(self) -> instructor.AsyncInstructor:
        # Ollama exposes OpenAI-compatible routes at /v1; instructor's
        # "ollama/..." provider talks to those.
        client = instructor.from_provider(
            f"ollama/{self._config.model}",
            base_url=f"{self._host}/v1",
            async_client=True,
            mode=instructor.Mode.JSON,
        )
        assert isinstance(client, instructor.AsyncInstructor)
        return client

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
            provider="ollama",
            model=model or self._config.model,
            workflow=workflow,
            latency_ms=latency_ms,
            outcome=outcome,  # type: ignore[arg-type]
        )

    # --- LLMProvider API -------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Verify the configured model is pulled and round-trips a
        trivial structured prompt. Logs one ``llm_calls`` row regardless
        of outcome."""
        t0 = time.perf_counter()
        try:
            tags = await self._native_client().list()
            available = {m.model for m in tags.models if m.model is not None}
            if self._config.model not in available:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                self._log("health_check", latency_ms, outcome="error")
                return HealthStatus(
                    status="down",
                    detail=f"model {self._config.model!r} not pulled; available: {sorted(available)}",
                    model=self._config.model,
                    latency_ms=latency_ms,
                )
            client = self._instructor_client()
            resp: _SmokeResponse = await client.chat.completions.create(
                messages=[{"role": "user", "content": _SMOKE_PROMPT}],
                response_model=_SmokeResponse,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if not resp.ok:
                self._log("health_check", latency_ms, outcome="malformed")
                return HealthStatus(
                    status="degraded",
                    detail=f"smoke round-trip returned ok=False ({resp!r})",
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
        raise NotImplementedError("OllamaProvider.rank arrives in phase3-03")

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        raise NotImplementedError("OllamaProvider.update_profile arrives in phase3-05")

    async def parse_search(self, query: str) -> SearchFilter:
        raise NotImplementedError("OllamaProvider.parse_search arrives in phase4")

    # --- debugging escape hatch -----------------------------------------

    async def raw_generate(self, prompt: str, **kw: Any) -> str:
        """Non-structured generate for debugging. Not part of the
        Protocol."""
        t0 = time.perf_counter()
        resp = await self._native_client().generate(model=self._config.model, prompt=prompt, **kw)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._log("raw_generate", latency_ms)
        return str(resp.response)
