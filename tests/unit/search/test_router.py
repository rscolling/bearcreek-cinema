"""route_query decision table.

30+ golden-path fixtures covering title / descriptive / play_command
/ more-like-X, exercising heuristics and the FTS probe seam.
"""

from __future__ import annotations

import pytest

from archive_agent.search.router import (
    QueryIntent,
    RoutingDecision,
    route_query,
)


def _no_probe(q: str) -> bool:
    return False


def _always_probe(q: str) -> bool:
    return True


# --- heuristic: play verbs ------------------------------------------------


@pytest.mark.parametrize(
    "query,stripped",
    [
        ("play The Third Man", "The Third Man"),
        ("watch Casablanca", "Casablanca"),
        ("Play His Girl Friday", "His Girl Friday"),
        ("Watch  The Thin Man", "The Thin Man"),
        ("put on Nosferatu", "Nosferatu"),
    ],
)
async def test_play_verbs_classified_as_play_command(
    query: str, stripped: str
) -> None:
    result = await route_query(query)
    assert result.intent == QueryIntent.PLAY_COMMAND
    # Normalization lowercases, so stripped comes back lowercase too.
    assert result.stripped_query == stripped.lower()


# --- heuristic: more-like-X -----------------------------------------------


@pytest.mark.parametrize(
    "query,anchor",
    [
        ("more like The Third Man", "the third man"),
        ("similar to Casablanca", "casablanca"),
        ("more of The Thin Man", "the thin man"),
        ("something like noir classics", "noir classics"),
    ],
)
async def test_more_like_is_descriptive_with_anchor(
    query: str, anchor: str
) -> None:
    result = await route_query(query)
    assert result.intent == QueryIntent.DESCRIPTIVE
    assert result.anchor_query == anchor


# --- heuristic: descriptive terms -----------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "noir",
        "something funny",
        "scary horror",
        "old comedy",
        "pre-code drama",
        "short documentary",
        "a fifties western",
    ],
)
async def test_descriptive_terms_trigger_descriptive_intent(
    query: str,
) -> None:
    result = await route_query(query)
    assert result.intent == QueryIntent.DESCRIPTIVE
    assert result.anchor_query is None


# --- FTS probe ------------------------------------------------------------


async def test_short_alphanumeric_title_hits_fts_probe() -> None:
    result = await route_query("third man", fts_probe=_always_probe)
    assert result.intent == QueryIntent.TITLE
    assert result.reasoning == "fts_probe"


async def test_short_query_without_fts_probe_match_falls_through_to_title() -> None:
    """No FTS hit and no other heuristic match → default title."""
    result = await route_query("unknown query", fts_probe=_no_probe)
    assert result.intent == QueryIntent.TITLE
    assert result.reasoning == "default_title"


async def test_long_ambiguous_query_skips_probe() -> None:
    """6+ tokens → not a plausible title; probe isn't called."""
    probe_calls: list[str] = []

    def _probe(q: str) -> bool:
        probe_calls.append(q)
        return True

    result = await route_query(
        "what should i watch this weekend", fts_probe=_probe
    )
    assert probe_calls == []  # short-alpha check gated it
    # No descriptive terms either → default title.
    assert result.intent == QueryIntent.TITLE


# --- LLM classifier seam --------------------------------------------------


async def test_llm_classifier_fills_unknowns() -> None:
    async def _classify(q: str) -> RoutingDecision:
        return RoutingDecision(
            intent=QueryIntent.UNKNOWN, reasoning="llm says unclear"
        )

    result = await route_query(
        "ambiguous six word query here", llm_classify=_classify
    )
    assert result.intent == QueryIntent.UNKNOWN
    assert result.reasoning == "llm says unclear"


async def test_llm_classifier_failure_falls_back_to_default_title() -> None:
    async def _broken(q: str) -> RoutingDecision:
        raise RuntimeError("model down")

    result = await route_query(
        "seven words that dont hit any heuristic",
        llm_classify=_broken,
    )
    assert result.intent == QueryIntent.TITLE
    assert result.reasoning == "default_title"


async def test_llm_classifier_can_assign_play_command() -> None:
    async def _classify(q: str) -> RoutingDecision:
        return RoutingDecision(intent=QueryIntent.PLAY_COMMAND, reasoning="llm")

    result = await route_query("some ambiguous input", llm_classify=_classify)
    assert result.intent == QueryIntent.PLAY_COMMAND


# --- edge cases -----------------------------------------------------------


async def test_empty_query_is_unknown() -> None:
    result = await route_query("")
    assert result.intent == QueryIntent.UNKNOWN
    assert result.normalized_query == ""


async def test_whitespace_only_query_is_unknown() -> None:
    result = await route_query("     ")
    assert result.intent == QueryIntent.UNKNOWN


async def test_normalization_runs_before_heuristics() -> None:
    """"3rd Man" → "third man", still matches default title path."""
    result = await route_query("3rd Man")
    assert result.normalized_query == "third man"


# --- ordering: more-like beats descriptive-term ---------------------------


async def test_more_like_beats_noir_term() -> None:
    """"more like my favorite noir" should be more-like, not bare
    descriptive — anchor is preserved."""
    result = await route_query("more like my favorite noir")
    assert result.intent == QueryIntent.DESCRIPTIVE
    assert result.anchor_query == "my favorite noir"


async def test_play_verb_beats_descriptive_term() -> None:
    """"play something funny" → PLAY_COMMAND with stripped query."""
    result = await route_query("play something funny")
    assert result.intent == QueryIntent.PLAY_COMMAND
    assert result.stripped_query == "something funny"
