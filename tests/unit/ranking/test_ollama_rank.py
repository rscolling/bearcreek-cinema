"""OllamaProvider.rank — structured output, fallbacks, rating priors.

No real HTTP: we monkey-patch ``_instructor_client`` to a stub that
returns canned responses (or raises canned errors). The provider's
contract is what we're asserting, not Ollama's.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from archive_agent.config import Config
from archive_agent.ranking.ollama_provider import OllamaProvider
from archive_agent.ranking.prompts.rank import RankItem, RankResponse, build_rank_prompt
from archive_agent.state.models import (
    Candidate,
    ContentType,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)

_NOW = datetime(2026, 4, 19, tzinfo=UTC)


def _candidate(archive_id: str, title: str, **overrides: Any) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": ContentType.MOVIE,
        "title": title,
        "year": 1950,
        "runtime_minutes": 95,
        "genres": ["Drama"],
        "source_collection": "moviesandfilms",
        "discovered_at": _NOW,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _profile() -> TasteProfile:
    return TasteProfile(
        version=1,
        updated_at=_NOW,
        liked_genres=["Noir", "Comedy"],
        summary="Household likes crisp dialogue and mid-century pacing.",
    )


class _StubCompletions:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior
        self.calls = 0

    async def create(
        self, *, messages: list[dict[str, str]], response_model: type, **_: Any
    ) -> Any:
        self.calls += 1
        if callable(self._behavior):
            return self._behavior(messages, response_model)
        return self._behavior


class _StubChat:
    def __init__(self, completions: _StubCompletions) -> None:
        self.completions = completions


class _StubInstructorClient:
    def __init__(self, behavior: Any) -> None:
        self.chat = _StubChat(_StubCompletions(behavior))


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, provider: OllamaProvider, behavior: Any
) -> _StubInstructorClient:
    stub = _StubInstructorClient(behavior)
    monkeypatch.setattr(provider, "_instructor_client", lambda: stub)
    return stub


async def test_happy_path_returns_ranked_picks(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [
        _candidate("n1", "Shadow Alley", genres=["Noir"]),
        _candidate("n2", "Night Street", genres=["Noir"]),
        _candidate("c1", "Funny Business", genres=["Comedy"]),
    ]
    response = RankResponse(
        picks=[
            RankItem(archive_id="n1", score=0.91, reasoning="Classic postwar noir mood."),
            RankItem(
                archive_id="c1",
                score=0.72,
                reasoning="Screwball dialogue anchors the household's stated love of pace.",
            ),
        ]
    )
    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, response)

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert len(ranked) == 2
    assert [r.candidate.archive_id for r in ranked] == ["n1", "c1"]
    assert [r.rank for r in ranked] == [1, 2]
    assert all(0.0 <= r.score <= 1.0 for r in ranked)
    assert all(r.reasoning for r in ranked)


async def test_malformed_output_falls_back(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [_candidate(f"m{i}", f"Film {i}") for i in range(3)]

    def _raise_validation(*_: Any, **__: Any) -> Any:
        raise ValidationError.from_exception_data("RankResponse", [])

    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, _raise_validation)

    ranked = await provider.rank(_profile(), candidates, n=3)

    # Fallback: first 3 candidates, templated reasoning, no raise.
    assert len(ranked) == 3
    assert [r.candidate.archive_id for r in ranked] == ["m0", "m1", "m2"]
    assert all(r.reasoning == "Fallback: similarity match." for r in ranked)

    # llm_calls row captures the malformed outcome.
    row = db.execute(
        "SELECT provider, workflow, outcome FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["provider"] == "ollama"
    assert row["workflow"] == "rank"
    assert row["outcome"] == "malformed"


async def test_timeout_falls_back(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [_candidate(f"t{i}", f"Film {i}") for i in range(3)]

    def _raise_timeout(*_: Any, **__: Any) -> Any:
        raise TimeoutError("ollama took too long")

    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, _raise_timeout)

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert len(ranked) == 2
    row = db.execute("SELECT outcome FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["outcome"] == "timeout"


async def test_hallucinated_ids_are_dropped(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [_candidate("real1", "Real Film"), _candidate("real2", "Another")]
    response = RankResponse(
        picks=[
            RankItem(archive_id="ghost", score=0.95, reasoning="Made-up by the model."),
            RankItem(archive_id="real1", score=0.82, reasoning="Actually in the shortlist."),
        ]
    )
    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, response)

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert [r.candidate.archive_id for r in ranked] == ["real1", "real2"]
    # Second slot padded from prefilter order with fallback reasoning.
    assert ranked[1].reasoning == "Fallback: similarity match."


async def test_rating_injection_appears_in_prompt() -> None:
    """Prompt-level contract — ratings within the window land in the text."""
    cand = _candidate(
        "show_ep",
        "Detective Show",
        content_type=ContentType.SHOW,
        show_id="showA",
    )
    fresh_rating = TasteEvent(
        timestamp=_NOW - timedelta(days=30),
        content_type=ContentType.SHOW,
        show_id="showA",
        kind=TasteEventKind.RATED_LOVE,
        strength=1.0,
        source="roku_api",
    )

    prompt = build_rank_prompt(_profile(), [cand], n=1, ratings={"showA": fresh_rating}, now=_NOW)

    # The LOVE glyph appears in the candidate block (and in instructions).
    # Candidate line is the only place "id=show_ep" appears, and the tag
    # must sit on that same line.
    candidate_line = next(line for line in prompt.splitlines() if "id=show_ep" in line)
    assert "[rated: 👍👍]" in candidate_line


async def test_old_ratings_are_not_injected() -> None:
    cand = _candidate(
        "show_ep",
        "Detective Show",
        content_type=ContentType.SHOW,
        show_id="showA",
    )
    stale_rating = TasteEvent(
        timestamp=_NOW - timedelta(days=365),
        content_type=ContentType.SHOW,
        show_id="showA",
        kind=TasteEventKind.RATED_DOWN,
        strength=0.9,
        source="roku_api",
    )

    prompt = build_rank_prompt(_profile(), [cand], n=1, ratings={"showA": stale_rating}, now=_NOW)

    candidate_line = next(line for line in prompt.splitlines() if "id=show_ep" in line)
    assert "[rated:" not in candidate_line


async def test_empty_candidates_returns_empty(config: Config, db: sqlite3.Connection) -> None:
    provider = OllamaProvider(config.llm.ollama, conn=db)
    assert await provider.rank(_profile(), [], n=5) == []


async def test_n_greater_than_candidates_truncates(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [_candidate("a1", "A"), _candidate("a2", "B")]
    response = RankResponse(
        picks=[
            RankItem(archive_id="a1", score=0.9, reasoning="First pick reasoning here."),
            RankItem(archive_id="a2", score=0.8, reasoning="Second pick reasoning here."),
        ]
    )
    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, response)

    ranked = await provider.rank(_profile(), candidates, n=10)

    assert len(ranked) == 2  # capped at shortlist length


async def test_llm_calls_row_written_on_success(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [_candidate("s1", "S1"), _candidate("s2", "S2")]
    response = RankResponse(
        picks=[
            RankItem(archive_id="s1", score=0.9, reasoning="reasoning one here yes."),
            RankItem(archive_id="s2", score=0.8, reasoning="reasoning two here yes."),
        ]
    )
    provider = OllamaProvider(config.llm.ollama, conn=db)
    _patch_client(monkeypatch, provider, response)

    await provider.rank(_profile(), candidates, n=2)

    row = db.execute(
        "SELECT provider, workflow, outcome FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["provider"] == "ollama"
    assert row["workflow"] == "rank"
    assert row["outcome"] == "ok"
