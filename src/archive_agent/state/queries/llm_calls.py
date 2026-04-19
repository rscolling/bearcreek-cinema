"""Audit-log inserts for every LLM call (phase1-05 wires this in).

The ``llm_calls`` table is append-only observability data — there are no
update or delete paths here by design.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

Provider = Literal["ollama", "claude", "tfidf"]
Outcome = Literal["ok", "malformed", "timeout", "error", "fallback"]


def insert(
    conn: sqlite3.Connection,
    *,
    provider: Provider,
    model: str,
    workflow: str,
    latency_ms: int,
    outcome: Outcome = "ok",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO llm_calls (
            timestamp, provider, model, workflow, latency_ms,
            input_tokens, output_tokens, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(),
            provider,
            model,
            workflow,
            latency_ms,
            input_tokens,
            output_tokens,
            outcome,
        ),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT produced no lastrowid — schema drift?")
    return int(rowid)
