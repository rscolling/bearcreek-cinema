"""``audit_llm_call`` context manager semantics."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator

import pytest

from archive_agent.ranking.audit import audit_llm_call
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


def _rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT provider, model, workflow, outcome, latency_ms, "
        "input_tokens, output_tokens FROM llm_calls ORDER BY id"
    ).fetchall()


async def test_happy_path_writes_row(db: sqlite3.Connection) -> None:
    async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db) as ctx:
        ctx.input_tokens = 42
        ctx.output_tokens = 99
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["provider"] == "ollama"
    assert rows[0]["workflow"] == "rank"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["input_tokens"] == 42
    assert rows[0]["output_tokens"] == 99
    assert rows[0]["latency_ms"] >= 0


async def test_exception_writes_error_row_and_re_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db):
            raise RuntimeError("boom")
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"


async def test_timeout_error_sets_timeout_outcome(db: sqlite3.Connection) -> None:
    with pytest.raises(asyncio.TimeoutError):
        async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db):
            raise TimeoutError
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "timeout"


async def test_explicit_outcome_overrides_default(db: sqlite3.Connection) -> None:
    async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db) as ctx:
        ctx.outcome = "malformed"
    rows = _rows(db)
    assert rows[0]["outcome"] == "malformed"


async def test_explicit_fallback_outcome_allowed(db: sqlite3.Connection) -> None:
    async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db) as ctx:
        ctx.outcome = "fallback"
    rows = _rows(db)
    assert rows[0]["outcome"] == "fallback"


async def test_conn_none_is_silent() -> None:
    """No conn → no row, still times and exits cleanly."""
    async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=None) as ctx:
        assert ctx.latency_ms >= 0


async def test_latency_ms_property_monotonic(db: sqlite3.Connection) -> None:
    async with audit_llm_call("ollama", "qwen2.5:7b", "rank", conn=db) as ctx:
        before = ctx.latency_ms
        await asyncio.sleep(0.01)
        after = ctx.latency_ms
        assert after >= before
