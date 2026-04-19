"""Shared fixtures for jellyfin/ tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "claude-code-pack" / "fixtures"


@pytest.fixture
def sample_history_json() -> dict[str, object]:
    return json.loads((_FIXTURE_DIR / "sample_jellyfin_history.json").read_text())
