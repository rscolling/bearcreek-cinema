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
from pydantic import BaseModel, ValidationError

from archive_agent.config import LlmOllamaConfig
from archive_agent.logging import get_logger
from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.prompts import RankResponse, build_rank_prompt
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.testing.token_budget import check_prompt_fits

__all__ = ["OllamaProvider"]

_log = get_logger("archive_agent.ranking.ollama")


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
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        """Rerank the shortlist via qwen2.5:7b structured output.

        Contract (CONTRACTS.md §2): never raises for malformed output.
        On total failure, returns a trivial ordering from ``candidates``
        (which the caller should pass sorted by prefilter score) with
        templated reasoning.
        """
        if not candidates:
            return []

        n_requested = min(n, len(candidates))
        prompt = build_rank_prompt(profile, candidates, n=n_requested, ratings=ratings)
        check_prompt_fits(prompt, num_ctx=self._config.num_ctx, margin_pct=0.2)

        try:
            client = self._instructor_client()
        except Exception as exc:
            _log.error("ollama_rank_client_error", error=str(exc))
            return self._fallback_ranking(candidates, n_requested, reason=f"client: {exc}")

        async with audit_llm_call(
            "ollama", self._config.model, "rank", conn=self._conn
        ) as ctx:
            try:
                resp: RankResponse = await client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=RankResponse,
                    max_retries=self._config.max_retries,
                )
            except ValidationError as exc:
                ctx.outcome = "malformed"
                _log.warning("ollama_rank_malformed", error=str(exc))
                return self._fallback_ranking(candidates, n_requested, reason="malformed")
            except TimeoutError:
                ctx.outcome = "timeout"
                _log.warning("ollama_rank_timeout")
                return self._fallback_ranking(candidates, n_requested, reason="timeout")
            except Exception as exc:
                ctx.outcome = "error"
                _log.warning("ollama_rank_error", error=str(exc))
                return self._fallback_ranking(candidates, n_requested, reason=f"error: {exc}")

        return _response_to_ranked(resp, candidates, n_requested) or self._fallback_ranking(
            candidates, n_requested, reason="no valid picks"
        )

    def _fallback_ranking(
        self, candidates: list[Candidate], n: int, *, reason: str
    ) -> list[RankedCandidate]:
        """Pick the first ``n`` candidates as-is. Caller is expected to
        have passed them prefilter-sorted, so first == best similarity.
        """
        _log.info("ollama_rank_fallback", reason=reason, n=n)
        ranked: list[RankedCandidate] = []
        for i, c in enumerate(candidates[:n]):
            ranked.append(
                RankedCandidate(
                    candidate=c,
                    score=max(0.1, 1.0 - i * 0.1),
                    reasoning="Fallback: similarity match.",
                    rank=i + 1,
                )
            )
        return ranked

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


def _response_to_ranked(
    resp: RankResponse, candidates: list[Candidate], n: int
) -> list[RankedCandidate]:
    """Merge instructor's ``RankResponse`` with the candidate objects.

    - Drops picks whose ``archive_id`` isn't in ``candidates`` (hallucinated).
    - Re-sorts by score descending and renumbers ranks 1..n — we don't
      trust the LLM's rank ordering.
    - Truncates to ``n``.
    - Pads from candidates (in prefilter order) if <n survive, so callers
      always get their requested count when there's material to draw on.
    """
    by_id = {c.archive_id: c for c in candidates}
    picked_ids: set[str] = set()
    picks: list[RankedCandidate] = []

    for item in sorted(resp.picks, key=lambda p: p.score, reverse=True):
        cand = by_id.get(item.archive_id)
        if cand is None or cand.archive_id in picked_ids:
            continue
        picks.append(
            RankedCandidate(
                candidate=cand,
                score=item.score,
                reasoning=item.reasoning,
                rank=len(picks) + 1,
            )
        )
        picked_ids.add(cand.archive_id)
        if len(picks) >= n:
            break

    if not picks:
        return []

    # Pad from prefilter order if the model under-picked.
    for c in candidates:
        if len(picks) >= n:
            break
        if c.archive_id in picked_ids:
            continue
        picks.append(
            RankedCandidate(
                candidate=c,
                score=max(0.1, 1.0 - len(picks) * 0.1),
                reasoning="Fallback: similarity match.",
                rank=len(picks) + 1,
            )
        )
        picked_ids.add(c.archive_id)

    return picks
