"""Three-stage query router: heuristics → FTS probe → LLM fallback.

This card (phase4-08) ships the first two stages end-to-end. The LLM
classifier stage is wired as an optional coroutine callback so a
follow-up can slot in a real small-model call without changing the
call sites — for now, when heuristics can't decide, we tag the query
``UNKNOWN`` and let the endpoint fall back to title search.

The router is deliberately dumb: it classifies and extracts; the
endpoint executes.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from archive_agent.search.descriptive_terms import DESCRIPTIVE_TERMS
from archive_agent.search.normalize import normalize_query
from archive_agent.state.models import SearchFilter


class QueryIntent(StrEnum):
    TITLE = "title"
    DESCRIPTIVE = "descriptive"
    PLAY_COMMAND = "play"
    UNKNOWN = "unknown"


class QueryRouteResult(BaseModel):
    intent: QueryIntent
    normalized_query: str
    # For ``DESCRIPTIVE`` with a "more like X" pattern, anchor_query
    # holds the extracted X so the endpoint can FTS-resolve it to an
    # archive_id before dispatching to the similarity pipeline.
    anchor_query: str | None = None
    filter: SearchFilter | None = None
    # When the user typed a play/watch verb the bare title is what
    # the endpoint actually matches against.
    stripped_query: str | None = None
    reasoning: str = Field(default="heuristic")


# ``fts_probe`` is an injected callable so tests can stub it without
# standing up a real DB. It takes a query string and returns True iff
# the query is a strong title match (caller defines "strong").
FtsProbeFn = Callable[[str], bool]


# Optional LLM classifier seam. Real implementation lands in a
# follow-up card — the call signature mirrors ``RoutingDecision``
# from phase4-08's spec. ``None`` (the default) means "skip the LLM
# stage entirely".
class RoutingDecision(BaseModel):
    intent: QueryIntent
    anchor_query: str | None = None
    reasoning: str = "llm"


LlmClassifyFn = Callable[[str], Awaitable[RoutingDecision]]


_PLAY_VERB_RE = re.compile(r"^\s*(play|watch|put on)\s+(?P<rest>.+)$", re.IGNORECASE)
_MORE_LIKE_RE = re.compile(
    r"^\s*(more\s+(?:like|of)|similar\s+to|something\s+like)\s+(?P<anchor>.+)$",
    re.IGNORECASE,
)


def _token_set(q: str) -> set[str]:
    # Treat hyphen-joined tokens as their own items so "neo-noir"
    # matches the curated list.
    return {t for t in re.split(r"\s+", q) if t}


def _heuristic(query: str) -> QueryRouteResult | None:
    """Fast first pass — regex-level patterns. Returns ``None`` when
    the query doesn't match any heuristic and the caller should
    escalate to the FTS probe / LLM classifier."""
    # "more like X" / "similar to X" — always descriptive + anchor.
    if m := _MORE_LIKE_RE.match(query):
        anchor = m.group("anchor").strip().strip("\"'.,!?")
        return QueryRouteResult(
            intent=QueryIntent.DESCRIPTIVE,
            normalized_query=query,
            anchor_query=anchor,
            reasoning="heuristic:more_like",
        )

    # "play ..." / "watch ..."
    if m := _PLAY_VERB_RE.match(query):
        stripped = m.group("rest").strip()
        return QueryRouteResult(
            intent=QueryIntent.PLAY_COMMAND,
            normalized_query=query,
            stripped_query=stripped,
            reasoning="heuristic:play_verb",
        )

    tokens = _token_set(query)

    # Descriptive term present → dispatch to descriptive pipeline.
    if tokens & DESCRIPTIVE_TERMS:
        return QueryRouteResult(
            intent=QueryIntent.DESCRIPTIVE,
            normalized_query=query,
            reasoning="heuristic:descriptive_term",
        )

    return None


def _short_alphanumeric_title(query: str) -> bool:
    """Heuristic: 1-5 tokens, all alphanumeric or apostrophe/hyphen.
    Bias queries like "the third man" toward TITLE before the LLM
    ever gets asked."""
    tokens = [t for t in query.split() if t]
    if not 1 <= len(tokens) <= 5:
        return False
    return all(re.fullmatch(r"[a-z0-9][a-z0-9'\-]*", t) for t in tokens)


async def route_query(
    query: str,
    *,
    fts_probe: FtsProbeFn | None = None,
    llm_classify: LlmClassifyFn | None = None,
) -> QueryRouteResult:
    """Classify ``query`` into a ``QueryIntent`` + extract facets.

    Three stages, short-circuit on success:

    1. Heuristic regex (play verb, more-like-X, descriptive term).
    2. FTS probe — strong title match → ``TITLE``.
    3. LLM classifier — reserved for ambiguous queries. Optional; when
       omitted or it raises, we fall back to ``UNKNOWN``.
    """
    normalized = normalize_query(query)
    if not normalized:
        return QueryRouteResult(
            intent=QueryIntent.UNKNOWN,
            normalized_query="",
            reasoning="empty",
        )

    hit = _heuristic(normalized)
    if hit is not None:
        return hit

    # Short, alphanumeric-only queries look like titles. Give FTS a
    # first look before escalating.
    if _short_alphanumeric_title(normalized) and fts_probe is not None and fts_probe(normalized):
        return QueryRouteResult(
            intent=QueryIntent.TITLE,
            normalized_query=normalized,
            reasoning="fts_probe",
        )

    # LLM stage — optional; only called when the earlier stages don't
    # converge. Real small-model integration lands in a follow-up.
    if llm_classify is not None:
        try:
            decision = await llm_classify(normalized)
        except Exception:
            decision = None
        if decision is not None:
            return QueryRouteResult(
                intent=decision.intent,
                normalized_query=normalized,
                anchor_query=decision.anchor_query,
                reasoning=decision.reasoning,
            )

    # Nothing to go on — default to TITLE intent (safer than UNKNOWN:
    # the endpoint can still return FTS hits). This mirrors the
    # common case of a short natural-language query that didn't trip
    # a descriptive term.
    return QueryRouteResult(
        intent=QueryIntent.TITLE,
        normalized_query=normalized,
        reasoning="default_title",
    )


__all__ = [
    "FtsProbeFn",
    "LlmClassifyFn",
    "QueryIntent",
    "QueryRouteResult",
    "RoutingDecision",
    "route_query",
]
