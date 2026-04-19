"""Scaffold-level smoke test: the package imports and its version is pinned."""

from __future__ import annotations

import archive_agent


def test_package_version() -> None:
    assert archive_agent.__version__ == "0.1.0"


def test_main_app_importable() -> None:
    from archive_agent.__main__ import app

    assert app.info.name == "archive-agent"
