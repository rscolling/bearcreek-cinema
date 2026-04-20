"""Query normalization — lowercase, whitespace, numerals."""

from __future__ import annotations

import pytest

from archive_agent.search.normalize import normalize_query


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("The Third Man", "the third man"),
        ("  multiple   spaces   ", "multiple spaces"),
        ("hello!?", "hello"),
        ("", ""),
        ("???", ""),
        ("3rd rock from the sun", "third rock from the sun"),
        ("2 fast 2 furious", "two fast two furious"),
        ("1949 noir", "1949 noir"),  # year stays
        ("play 1940s noir", "play 1940s noir"),
        ("The Thin Man!", "the thin man"),
    ],
)
def test_normalize_canonical_forms(raw: str, expected: str) -> None:
    assert normalize_query(raw) == expected


def test_normalize_preserves_years_as_digits() -> None:
    assert "1940" in normalize_query("anything from 1940 or so")


def test_normalize_is_idempotent() -> None:
    once = normalize_query("The Third Man!")
    twice = normalize_query(once)
    assert once == twice
