"""Every provider call (even health_check) writes a row to llm_calls.

These tests stay offline by monkey-patching the provider to avoid real
HTTP. The point is the logging contract, not the round-trip — that's
covered by the integration test.
"""

from __future__ import annotations

import sqlite3

import pytest

from archive_agent.config import Config
from archive_agent.ranking import ClaudeProvider, OllamaProvider, TFIDFProvider


def _llm_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT provider, model, workflow, outcome FROM llm_calls ORDER BY id"
    ).fetchall()


async def test_ollama_health_logs_even_on_error(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point at an unreachable host so the call fails fast
    config.llm.ollama.host = "http://127.0.0.1:1"
    provider = OllamaProvider(config.llm.ollama, conn=db)
    status = await provider.health_check()
    assert status.status == "down"
    rows = _llm_rows(db)
    assert len(rows) == 1
    assert rows[0]["provider"] == "ollama"
    assert rows[0]["workflow"] == "health_check"
    assert rows[0]["outcome"] == "error"


async def test_claude_health_without_key_does_not_log(
    config: Config, db: sqlite3.Connection
) -> None:
    # config.llm.claude.api_key is None in the fixture
    provider = ClaudeProvider(config.llm.claude, conn=db)
    status = await provider.health_check()
    assert status.status == "down"
    assert "ANTHROPIC_API_KEY not set" in status.detail
    # No HTTP call made → no llm_calls row
    assert _llm_rows(db) == []


async def test_tfidf_health_always_ok_and_logs(db: sqlite3.Connection) -> None:
    provider = TFIDFProvider(conn=db)
    status = await provider.health_check()
    assert status.status == "ok"
    rows = _llm_rows(db)
    assert len(rows) == 1
    assert rows[0]["provider"] == "tfidf"
    assert rows[0]["outcome"] == "ok"


async def test_provider_without_conn_is_silent(config: Config) -> None:
    """A provider built without a conn must still work — just no logs."""
    provider = TFIDFProvider(conn=None)
    status = await provider.health_check()
    assert status.status == "ok"


async def test_ollama_stub_methods_raise(config: Config, db: sqlite3.Connection) -> None:
    from datetime import UTC, datetime

    from archive_agent.state.models import TasteProfile

    provider = OllamaProvider(config.llm.ollama, conn=db)
    profile = TasteProfile(version=1, updated_at=datetime.now(UTC))

    with pytest.raises(NotImplementedError):
        await provider.rank(profile, [])
    with pytest.raises(NotImplementedError):
        await provider.update_profile(profile, [])
    with pytest.raises(NotImplementedError):
        await provider.parse_search("noir")
