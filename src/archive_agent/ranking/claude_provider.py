"""Anthropic Claude LLMProvider (optional, cloud).

Opt-in per workflow via ``[llm.workflows]`` — ADR-001 forbids silent
routing, so a missing ``api_key`` makes ``health_check`` return
``status=down`` without writing an ``llm_calls`` row. Only active
invocations cost money and only they land in the audit log.

Shared prompt builders (``ranking.prompts``) keep output shape
identical to ``OllamaProvider`` so the two are drop-in swappable.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import anthropic
import instructor
from pydantic import ValidationError

from archive_agent.config import LlmClaudeConfig
from archive_agent.logging import get_logger
from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.prompts import (
    RankResponse,
    build_rank_prompt,
    build_update_profile_prompt,
)
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

__all__ = ["ClaudeProvider", "estimate_cost_cents"]

_log = get_logger("archive_agent.ranking.claude")


# Per-model rates as $/Mtok (input, output). Verify against
# anthropic.com/pricing when rates change — this is a snapshot, not a
# live lookup. Unknown models price at 0 (surfaced by the CLI so it's
# obviously wrong).
_CLAUDE_COSTS_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


def estimate_cost_cents(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float:
    """Cost in US cents. Returns 0.0 for unknown models or missing counts."""
    if input_tokens is None or output_tokens is None:
        return 0.0
    rate = _CLAUDE_COSTS_PER_MTOK.get(model)
    if rate is None:
        # Best-effort: match on the model family prefix.
        for known, r in _CLAUDE_COSTS_PER_MTOK.items():
            if model.startswith(known.rsplit("-", 1)[0]):
                rate = r
                break
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    dollars = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000.0
    return round(dollars * 100.0, 4)


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        config: LlmClaudeConfig,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        self._config = config
        self._conn = conn

    # --- client factories ------------------------------------------------

    def _instructor_client(self) -> instructor.AsyncInstructor:
        if self._config.api_key is None:
            raise RuntimeError("ClaudeProvider requires llm.claude.api_key")
        client = instructor.from_provider(
            f"anthropic/{self._config.model}",
            async_client=True,
            api_key=self._config.api_key.get_secret_value(),
        )
        assert isinstance(client, instructor.AsyncInstructor)
        return client

    # --- LLMProvider API -------------------------------------------------

    async def health_check(self) -> HealthStatus:
        if self._config.api_key is None:
            # No audit row — per ADR-001, we don't log invocations that
            # never happened.
            return HealthStatus(
                status="down",
                detail="ANTHROPIC_API_KEY not set; ClaudeProvider is disabled",
                model=self._config.model,
            )
        async with audit_llm_call(
            "claude", self._config.model, "health_check", conn=self._conn
        ) as ctx:
            try:
                client = anthropic.AsyncAnthropic(
                    api_key=self._config.api_key.get_secret_value()
                )
                resp = await client.messages.create(
                    model=self._config.model,
                    max_tokens=16,
                    messages=[{"role": "user", "content": "Respond with exactly: OK"}],
                )
                text = getattr(resp.content[0], "text", "") if resp.content else ""
                ctx.input_tokens = resp.usage.input_tokens
                ctx.output_tokens = resp.usage.output_tokens
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
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        """Rerank via Claude structured output.

        Contract: never raises for malformed output — falls back to a
        trivial prefilter-order ranking with templated reasoning.
        """
        if not candidates:
            return []

        n_requested = min(n, len(candidates))
        prompt = build_rank_prompt(profile, candidates, n=n_requested, ratings=ratings)

        try:
            client = self._instructor_client()
        except Exception as exc:
            _log.error("claude_rank_client_error", error=str(exc))
            return _fallback_ranking(candidates, n_requested)

        async with audit_llm_call(
            "claude", self._config.model, "rank", conn=self._conn
        ) as ctx:
            try:
                resp, raw = await client.chat.completions.create_with_completion(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=RankResponse,
                    max_tokens=self._config.max_tokens,
                )
                _capture_usage(ctx, raw)
            except ValidationError as exc:
                ctx.outcome = "malformed"
                _log.warning("claude_rank_malformed", error=str(exc))
                return _fallback_ranking(candidates, n_requested)
            except TimeoutError:
                ctx.outcome = "timeout"
                _log.warning("claude_rank_timeout")
                return _fallback_ranking(candidates, n_requested)
            except Exception as exc:
                ctx.outcome = "error"
                _log.warning("claude_rank_error", error=type(exc).__name__)
                return _fallback_ranking(candidates, n_requested)

        return _response_to_ranked(resp, candidates, n_requested) or _fallback_ranking(
            candidates, n_requested
        )

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        """Evolve profile via Claude. Failure returns current + version bump."""
        prompt = build_update_profile_prompt(current, events)
        fallback = current.model_copy(
            update={
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )

        try:
            client = self._instructor_client()
        except Exception as exc:
            _log.error("claude_update_profile_client_error", error=str(exc))
            return fallback

        async with audit_llm_call(
            "claude", self._config.model, "update_profile", conn=self._conn
        ) as ctx:
            try:
                resp, raw = await client.chat.completions.create_with_completion(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=TasteProfile,
                    max_tokens=self._config.max_tokens,
                )
                _capture_usage(ctx, raw)
            except ValidationError as exc:
                ctx.outcome = "malformed"
                _log.warning("claude_update_profile_malformed", error=str(exc))
                return fallback
            except TimeoutError:
                ctx.outcome = "timeout"
                return fallback
            except Exception as exc:
                ctx.outcome = "error"
                _log.warning("claude_update_profile_error", error=type(exc).__name__)
                return fallback

        return resp.model_copy(
            update={
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )

    async def parse_search(self, query: str) -> SearchFilter:
        """Parse NL query to SearchFilter via Claude structured output."""
        prompt = (
            "Parse this search query into a structured SearchFilter. "
            "Return JSON only. Query: "
            f"{query!r}"
        )

        try:
            client = self._instructor_client()
        except Exception as exc:
            _log.error("claude_parse_search_client_error", error=str(exc))
            return SearchFilter(keywords=[query] if query else [])

        async with audit_llm_call(
            "claude", self._config.model, "parse_search", conn=self._conn
        ) as ctx:
            try:
                resp, raw = await client.chat.completions.create_with_completion(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=SearchFilter,
                    max_tokens=256,
                )
                _capture_usage(ctx, raw)
                return resp
            except ValidationError:
                ctx.outcome = "malformed"
                return SearchFilter(keywords=[query] if query else [])
            except Exception:
                ctx.outcome = "error"
                return SearchFilter(keywords=[query] if query else [])


# --- helpers ---------------------------------------------------------------


def _capture_usage(ctx: Any, raw: Any) -> None:
    """Pull input/output_tokens off the Anthropic raw message, if present."""
    usage = getattr(raw, "usage", None)
    if usage is None:
        return
    ctx.input_tokens = getattr(usage, "input_tokens", None)
    ctx.output_tokens = getattr(usage, "output_tokens", None)


def _response_to_ranked(
    resp: RankResponse, candidates: list[Candidate], n: int
) -> list[RankedCandidate]:
    """Merge the structured picks with candidate objects.

    Matches OllamaProvider._response_to_ranked semantics: drop
    hallucinated IDs, sort by score desc, renumber ranks, pad from
    prefilter order if short.
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


def _fallback_ranking(
    candidates: list[Candidate], n: int
) -> list[RankedCandidate]:
    """Trivial prefilter-order ranking with templated reasoning."""
    return [
        RankedCandidate(
            candidate=c,
            score=max(0.1, 1.0 - i * 0.1),
            reasoning="Fallback: similarity match.",
            rank=i + 1,
        )
        for i, c in enumerate(candidates[:n])
    ]
