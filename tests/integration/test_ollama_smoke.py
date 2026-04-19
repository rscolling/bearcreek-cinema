"""Live Ollama round-trip. Gated on RUN_INTEGRATION_TESTS=1.

Uses the real config + Ollama stack on don-quixote. The smoke test
round-trips a 2-field Pydantic model through the configured 7B model
to confirm the structured-output path works end-to-end.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to hit the real Ollama server",
    ),
]


async def test_health_check_round_trip() -> None:
    from archive_agent.config import load_config
    from archive_agent.ranking.factory import make_provider
    from archive_agent.state.db import connect
    from archive_agent.state.migrations import apply_pending

    cfg = load_config()
    conn = connect(":memory:")
    apply_pending(conn)

    provider = make_provider("ollama", cfg, conn=conn)
    status = await provider.health_check()

    assert status.status == "ok", f"health_check did not pass: {status}"
    assert status.model == cfg.llm.ollama.model
    assert status.latency_ms is not None
    assert status.latency_ms > 0

    rows = conn.execute(
        "SELECT provider, model, workflow, outcome, latency_ms FROM llm_calls"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "ollama"
    assert rows[0]["workflow"] == "health_check"
    assert rows[0]["outcome"] == "ok"
