"""Token-budget guard — simple heuristic, but the edge behavior matters."""

from __future__ import annotations

import pytest

from archive_agent.testing.token_budget import (
    PromptTooLargeError,
    check_prompt_fits,
    estimate_tokens,
)


def test_empty_prompt_is_zero_tokens() -> None:
    assert estimate_tokens("") == 0


def test_short_prompt_fits_comfortably() -> None:
    report = check_prompt_fits("Hello world", num_ctx=8192)
    assert report.fits
    assert report.prompt_tokens < report.budget_tokens


def test_budget_applies_margin() -> None:
    report = check_prompt_fits("x" * 100, num_ctx=1000, margin_pct=0.2)
    assert report.budget_tokens == 800


def test_oversized_prompt_raises() -> None:
    # 10000 chars ~ 2800 tokens, budget is 80% of 1000 = 800 → fail
    with pytest.raises(PromptTooLargeError):
        check_prompt_fits("x" * 10000, num_ctx=1000)


def test_raise_on_fail_false_returns_report() -> None:
    report = check_prompt_fits("x" * 10000, num_ctx=1000, raise_on_fail=False)
    assert not report.fits


def test_invalid_margin_raises() -> None:
    with pytest.raises(ValueError):
        check_prompt_fits("hi", num_ctx=1000, margin_pct=1.5)
