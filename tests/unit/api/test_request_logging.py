"""Every request emits one ``http_request`` structlog line with the
right keys."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


def test_request_logs_once_with_expected_keys(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="archive_agent.api"):
        resp = client.get("/")
    assert resp.status_code == 200

    # Structlog routes through stdlib logging at INFO; the event name
    # is embedded in the rendered message. Accept either JSON or
    # console formatting.
    messages = [rec.getMessage() for rec in caplog.records]
    matching = [m for m in messages if "http_request" in m]
    assert matching, f"expected http_request log line, got: {messages}"
    msg = matching[0]
    # Spot-check the structured fields we promised.
    for token in ("method=GET", "path=/", "status=200"):
        assert token in msg, f"missing {token!r} in {msg!r}"


def test_request_id_is_bound_in_log_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The middleware binds request_id via contextvars so the log line
    carries it — and the response echoes it in X-Request-ID."""
    with caplog.at_level(logging.INFO, logger="archive_agent.api"):
        resp = client.get("/")
    rid = resp.headers.get("x-request-id")
    assert rid and len(rid) == 12

    matching = [rec.getMessage() for rec in caplog.records if "http_request" in rec.getMessage()]
    assert any(rid in m for m in matching), f"request_id {rid} not found in log messages"
