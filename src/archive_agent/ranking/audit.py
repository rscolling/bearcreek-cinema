"""Context-manager wrapper that persists every LLM call to ``llm_calls``.

Providers use this to get free timing, outcome classification (``ok`` /
``malformed`` / ``timeout`` / ``error`` / ``fallback``), and the audit
row. Exceptions inside the ``async with`` block are re-raised after the
row is recorded — so ``outcome="error"`` is written even when the
caller lets the failure propagate. That means ``llm_calls`` stays a
reliable "was the model behaving last night?" source of truth regardless
of what the call itself did.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from archive_agent.logging import get_logger
from archive_agent.state.queries import llm_calls

__all__ = ["LLMCallContext", "Outcome", "audit_llm_call"]

Outcome = Literal["ok", "malformed", "timeout", "error", "fallback"]

log = get_logger("archive_agent.ranking.audit")


@dataclass
class LLMCallContext:
    """Mutable scratch passed to the ``async with`` block.

    The block may assign ``input_tokens``, ``output_tokens``, and
    ``outcome`` before it exits. ``outcome`` defaults to ``"ok"``; an
    uncaught exception promotes it to ``"error"`` (or ``"timeout"``).
    """

    provider: str
    model: str
    workflow: str
    _started_at: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: Outcome = "ok"

    @property
    def latency_ms(self) -> int:
        return int((time.perf_counter() - self._started_at) * 1000)


@asynccontextmanager
async def audit_llm_call(
    provider: str,
    model: str,
    workflow: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> AsyncIterator[LLMCallContext]:
    """Time an LLM call and write one row to ``llm_calls`` on exit.

    ``conn=None`` makes the logging a silent no-op, which is convenient
    for scripts that want the timing wrapper without DB side effects
    (including unit tests that care about exception behavior without
    setting up a schema).
    """
    ctx = LLMCallContext(
        provider=provider,
        model=model,
        workflow=workflow,
        _started_at=time.perf_counter(),
    )
    try:
        yield ctx
    except TimeoutError:
        ctx.outcome = "timeout"
        raise
    except Exception:
        ctx.outcome = "error"
        raise
    finally:
        latency_ms = ctx.latency_ms
        if conn is not None:
            try:
                llm_calls.insert(
                    conn,
                    provider=ctx.provider,  # type: ignore[arg-type]
                    model=ctx.model,
                    workflow=ctx.workflow,
                    latency_ms=latency_ms,
                    outcome=ctx.outcome,
                    input_tokens=ctx.input_tokens,
                    output_tokens=ctx.output_tokens,
                )
            except Exception as exc:  # pragma: no cover — defensive
                # Audit must never crash the caller. Log and move on.
                log.error(
                    "llm_call_audit_failed",
                    error=str(exc),
                    provider=ctx.provider,
                    workflow=ctx.workflow,
                )
        log.info(
            "llm_call",
            provider=ctx.provider,
            model=ctx.model,
            workflow=ctx.workflow,
            latency_ms=latency_ms,
            outcome=ctx.outcome,
            input_tokens=ctx.input_tokens,
            output_tokens=ctx.output_tokens,
        )
