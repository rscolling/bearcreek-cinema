"""Redaction processor + configure_logging behavior."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import structlog

from archive_agent.logging import (
    REDACT_FIELDS,
    configure_logging,
    get_logger,
    redact_processor,
)


def test_redact_basic_keys() -> None:
    event = {"event": "login", "api_key": "secret-123", "user": "alice"}
    out = redact_processor(logging.getLogger(), "info", event)
    assert out["api_key"] == "***"
    assert out["user"] == "alice"


def test_redact_variant_keys() -> None:
    """Redaction matches common variants — prefix/suffix/case don't matter."""
    event = {
        "JELLYFIN_API_KEY": "k1",
        "tmdb_token": "k2",
        "anthropic_API_KEY": "k3",
        "user_password": "k4",
        "MY_SECRET": "k5",
        "benign": "shown",
    }
    out = redact_processor(logging.getLogger(), "info", event)
    for k in ("JELLYFIN_API_KEY", "tmdb_token", "anthropic_API_KEY", "user_password", "MY_SECRET"):
        assert out[k] == "***", f"{k} not redacted"
    assert out["benign"] == "shown"


def test_redact_does_not_touch_tokens_counter() -> None:
    """Plural ``tokens`` is a counter (input_tokens / output_tokens), not a secret.

    Regression — the early implementation used substring matching and
    redacted these counters. See phase1-06 commit notes.
    """
    event = {"event": "llm_call", "input_tokens": 42, "output_tokens": 99}
    out = redact_processor(logging.getLogger(), "info", event)
    assert out["input_tokens"] == 42
    assert out["output_tokens"] == 99


def test_redact_walks_nested_dicts() -> None:
    event: dict[str, Any] = {
        "event": "config_loaded",
        "config": {
            "jellyfin": {"api_key": "jelly", "url": "http://host"},
            "tmdb": {"api_key": "tmdb-key"},
        },
    }
    out = redact_processor(logging.getLogger(), "info", event)
    assert out["config"]["jellyfin"]["api_key"] == "***"
    assert out["config"]["jellyfin"]["url"] == "http://host"
    assert out["config"]["tmdb"]["api_key"] == "***"


def test_redact_walks_lists_of_dicts() -> None:
    event = {"event": "e", "entries": [{"api_key": "k1"}, {"api_key": "k2"}]}
    out = redact_processor(logging.getLogger(), "info", event)
    assert [e["api_key"] for e in out["entries"]] == ["***", "***"]


def test_redact_fields_set_is_frozen() -> None:
    with pytest.raises(AttributeError):
        REDACT_FIELDS.add("new")  # type: ignore[attr-defined]


def test_json_output_redacts_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """Spot-check the full pipeline: configure → log → parsed JSON has
    redacted value for a sensitive key."""
    configure_logging(level="INFO", fmt="json")
    log = get_logger("phase1-06-redact-test")
    log.info("example_event", api_key="super-secret", user="alice")
    out = capsys.readouterr().err.strip()
    # Last non-empty line should be our event
    parsed = json.loads(out.splitlines()[-1])
    assert parsed["api_key"] == "***"
    assert parsed["user"] == "alice"
    # Reset to defaults so other tests don't inherit our config
    structlog.reset_defaults()


def test_console_renderer_does_not_leak_secret(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", fmt="console")
    log = get_logger("phase1-06-console-test")
    log.info("config_loaded", token="should-not-appear")
    out = capsys.readouterr().err
    assert "should-not-appear" not in out
    assert "***" in out
    structlog.reset_defaults()
