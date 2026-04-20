"""Query text normalization — run before everything else.

Cheap, deterministic, reversible-ish: lowercase + whitespace + common
ASR oddities. Year tokens stay numeric so the era parser downstream
can still catch "1949" or "1940-1959"; only non-year numbers get
spelled out.
"""

from __future__ import annotations

import re

_ORDINAL_MAP = {
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
    "5th": "fifth",
    "6th": "sixth",
    "7th": "seventh",
    "8th": "eighth",
    "9th": "ninth",
    "10th": "tenth",
}

# Number words for small counts — common in show titles (Seven,
# Twelve) and voice queries ("two fast cars"). Leave years and
# larger numbers untouched.
_NUMERAL_MAP = {
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
}

_PUNCT_STRIP = re.compile(r"^[\s\W_]+|[\s\W_]+$")
_WHITESPACE = re.compile(r"\s+")


def _expand_token(tok: str) -> str:
    lower = tok.lower()
    if lower in _ORDINAL_MAP:
        return _ORDINAL_MAP[lower]
    # Only expand short numerals. Four-digit years and larger numbers
    # stay as digits so downstream regex can still spot them.
    if lower in _NUMERAL_MAP:
        return _NUMERAL_MAP[lower]
    return lower


def normalize_query(raw: str) -> str:
    """Return a canonical form of the query.

    - Lowercased
    - Collapsed whitespace
    - Leading / trailing punctuation stripped
    - Short numerals and ordinals spelled out ("3rd" → "third")
    - Year tokens (4 digits) left as-is
    """
    cleaned = _PUNCT_STRIP.sub("", raw)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().lower()
    if not cleaned:
        return ""
    tokens = [_expand_token(t) for t in cleaned.split(" ")]
    return " ".join(tokens)


__all__ = ["normalize_query"]
