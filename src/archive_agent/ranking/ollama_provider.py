"""Local Ollama LLMProvider (default).

Uses two client libraries side by side:

- ``ollama.AsyncClient`` for model listing + lifecycle probes (native API).
- ``instructor.AsyncInstructor`` over the OpenAI-compatible ``/v1/...``
  endpoint for structured-JSON round trips. Instructor handles
  Pydantic response-model coercion and retries on malformed output.

Every call goes through ``audit_llm_call`` (phase1-06) so one row lands
in ``llm_calls`` regardless of success or failure.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import instructor
import ollama
from pydantic import BaseModel

from archive_agent.config import LlmOllamaConfig
from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

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

    # --- LLMProvider API -------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Verify the configured model is pulled and round-trip a trivial
        structured prompt. One ``llm_calls`` row is written either way."""
        async with audit_llm_call(
            "ollama", self._config.model, "health_check", conn=self._conn
        ) as ctx:
            try:
                tags = await self._native_client().list()
                available = {m.model for m in tags.models if m.model is not None}
                if self._config.model not in available:
                    ctx.outcome = "error"
                    return HealthStatus(
                        status="down",
                        detail=f"model {self._config.model!r} not pulled; "
                        f"available: {sorted(available)}",
                        model=self._config.model,
                        latency_ms=ctx.latency_ms,
                    )
                client = self._instructor_client()
                resp: _SmokeResponse = await client.chat.completions.create(
                    messages=[{"role": "user", "content": _SMOKE_PROMPT}],
                    response_model=_SmokeResponse,
                )
                if not resp.ok:
                    ctx.outcome = "malformed"
                    return HealthStatus(
                        status="degraded",
                        detail=f"smoke round-trip returned ok=False ({resp!r})",
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
        """Non-structured generate for debugging. Not part of the Protocol."""
        async with audit_llm_call("ollama", self._config.model, "raw_generate", conn=self._conn):
            resp = await self._native_client().generate(
                model=self._config.model, prompt=prompt, **kw
            )
            return str(resp.response)
