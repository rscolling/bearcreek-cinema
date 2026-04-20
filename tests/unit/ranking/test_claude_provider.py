"""ClaudeProvider: mocked Anthropic round-trips, token capture, redaction."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from archive_agent.config import Config, LlmClaudeConfig
from archive_agent.ranking.claude_provider import (
    ClaudeProvider,
    estimate_cost_cents,
)
from archive_agent.ranking.prompts.rank import RankItem, RankResponse
from archive_agent.state.models import (
    Candidate,
    ContentType,
    TasteProfile,
)

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(archive_id: str, title: str = "Film", **overrides: Any) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": ContentType.MOVIE,
        "title": title,
        "year": 1950,
        "runtime_minutes": 95,
        "genres": ["Noir"],
        "source_collection": "moviesandfilms",
        "discovered_at": _NOW,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _profile() -> TasteProfile:
    return TasteProfile(version=1, updated_at=_NOW, liked_genres=["Noir"])


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeRaw:
    def __init__(self, input_tokens: int = 500, output_tokens: int = 150) -> None:
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _StubCompletions:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior
        self.calls = 0

    async def create_with_completion(
        self, *, messages: list[dict[str, str]], response_model: type, **_: Any
    ) -> tuple[Any, Any]:
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


def _with_key(config: Config) -> LlmClaudeConfig:
    config.llm.claude = LlmClaudeConfig(
        api_key=SecretStr("sk-ant-SECRET-KEY-NOT-REAL"),
        model="claude-sonnet-4-6",
    )
    return config.llm.claude


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, provider: ClaudeProvider, behavior: Any
) -> _StubInstructorClient:
    stub = _StubInstructorClient(behavior)
    monkeypatch.setattr(provider, "_instructor_client", lambda: stub)
    return stub


# --- cost estimator --------------------------------------------------------


def test_cost_estimator_known_model() -> None:
    # Sonnet: $3/Mtok in, $15/Mtok out. 500 in + 150 out =
    # 0.0015 + 0.00225 = 0.00375 USD = 0.375 cents.
    cents = estimate_cost_cents("claude-sonnet-4-6", 500, 150)
    assert 0.37 < cents < 0.38


def test_cost_estimator_missing_tokens_is_zero() -> None:
    assert estimate_cost_cents("claude-sonnet-4-6", None, 150) == 0.0
    assert estimate_cost_cents("claude-sonnet-4-6", 500, None) == 0.0


def test_cost_estimator_unknown_model_is_zero() -> None:
    assert estimate_cost_cents("gpt-7-ultra", 500, 150) == 0.0


def test_cost_estimator_prefix_match_for_near_known() -> None:
    # A version we don't know the exact suffix of still hits the family
    # rate via the prefix fallback.
    cents = estimate_cost_cents("claude-sonnet-4-9", 500, 150)
    assert cents > 0.0


# --- disabled provider -----------------------------------------------------


async def test_health_check_without_api_key_does_not_log(
    config: Config, db: sqlite3.Connection
) -> None:
    config.llm.claude = LlmClaudeConfig(api_key=None)
    provider = ClaudeProvider(config.llm.claude, conn=db)

    status = await provider.health_check()

    assert status.status == "down"
    assert "not set" in status.detail
    # No row written — we never invoked the API.
    assert db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


async def test_rank_without_api_key_falls_back(config: Config, db: sqlite3.Connection) -> None:
    config.llm.claude = LlmClaudeConfig(api_key=None)
    provider = ClaudeProvider(config.llm.claude, conn=db)
    candidates = [_candidate(f"m{i}") for i in range(3)]

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert len(ranked) == 2
    assert all(r.reasoning == "Fallback: similarity match." for r in ranked)


# --- rank happy / failure paths -------------------------------------------


async def test_rank_happy_path_captures_usage_and_cost(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    candidates = [_candidate("n1", genres=["Noir"]), _candidate("c1", genres=["Comedy"])]
    response = RankResponse(
        picks=[
            RankItem(
                archive_id="n1",
                score=0.9,
                reasoning="Dense noir pacing matches the household's liked list.",
            ),
            RankItem(
                archive_id="c1",
                score=0.7,
                reasoning="Light comedy balances the rest of the list.",
            ),
        ]
    )
    raw = _FakeRaw(input_tokens=600, output_tokens=200)
    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, (response, raw))

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert len(ranked) == 2
    assert [r.candidate.archive_id for r in ranked] == ["n1", "c1"]

    row = db.execute(
        "SELECT provider, model, workflow, outcome, input_tokens, output_tokens "
        "FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["provider"] == "claude"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["workflow"] == "rank"
    assert row["outcome"] == "ok"
    assert row["input_tokens"] == 600
    assert row["output_tokens"] == 200


async def test_rank_validation_error_falls_back(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    candidates = [_candidate(f"m{i}") for i in range(3)]

    def _raise(*_: Any, **__: Any) -> Any:
        raise ValidationError.from_exception_data("RankResponse", [])

    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, _raise)

    ranked = await provider.rank(_profile(), candidates, n=3)

    assert len(ranked) == 3
    assert all(r.reasoning == "Fallback: similarity match." for r in ranked)
    row = db.execute("SELECT outcome FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["outcome"] == "malformed"


async def test_rank_empty_candidates_returns_empty(config: Config, db: sqlite3.Connection) -> None:
    claude_cfg = _with_key(config)
    provider = ClaudeProvider(claude_cfg, conn=db)
    assert await provider.rank(_profile(), [], n=5) == []


async def test_hallucinated_ids_are_dropped(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    candidates = [_candidate("real1"), _candidate("real2")]
    response = RankResponse(
        picks=[
            RankItem(
                archive_id="ghost",
                score=0.95,
                reasoning="Not actually in the shortlist (hallucinated).",
            ),
            RankItem(
                archive_id="real1",
                score=0.85,
                reasoning="Real candidate that matches household taste.",
            ),
        ]
    )
    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, (response, _FakeRaw()))

    ranked = await provider.rank(_profile(), candidates, n=2)

    assert [r.candidate.archive_id for r in ranked] == ["real1", "real2"]
    # Padding slot falls back to similarity-match text.
    assert ranked[1].reasoning == "Fallback: similarity match."


# --- update_profile --------------------------------------------------------


async def test_update_profile_returns_new_version(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    current = TasteProfile(version=2, updated_at=_NOW, summary="old")
    response = TasteProfile(version=999, updated_at=_NOW, summary="new take")
    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, (response, _FakeRaw()))

    result = await provider.update_profile(current, [])

    assert result.version == 3  # authoritative: current.version + 1
    assert result.summary == "new take"


async def test_update_profile_failure_returns_bumped_current(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    current = TasteProfile(version=5, updated_at=_NOW, summary="keeper")

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError("network boom")

    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, _raise)

    result = await provider.update_profile(current, [])

    assert result.version == 6
    assert result.summary == "keeper"
    row = db.execute("SELECT outcome FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["outcome"] == "error"


# --- parse_search ----------------------------------------------------------


async def test_parse_search_happy_path(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)
    from archive_agent.state.models import SearchFilter

    response = SearchFilter(keywords=["noir", "heist"], era=(1940, 1959))
    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, (response, _FakeRaw(input_tokens=50, output_tokens=20)))

    result = await provider.parse_search("1940s heist noir")

    assert result.keywords == ["noir", "heist"]
    assert result.era == (1940, 1959)


async def test_parse_search_error_returns_keyword_fallback(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_cfg = _with_key(config)

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError("anthropic down")

    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, _raise)

    result = await provider.parse_search("space opera")

    assert result.keywords == ["space opera"]


# --- secret redaction -------------------------------------------------------


async def test_api_key_never_in_logs(
    config: Config,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing rank call must not leak the API key via logs."""
    claude_cfg = _with_key(config)

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError("kaboom")

    provider = ClaudeProvider(claude_cfg, conn=db)
    _patch_client(monkeypatch, provider, _raise)

    with caplog.at_level(logging.DEBUG):
        await provider.rank(_profile(), [_candidate("m1")], n=1)

    full_log = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "sk-ant-SECRET-KEY-NOT-REAL" not in full_log
