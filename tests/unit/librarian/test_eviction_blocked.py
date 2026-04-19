"""When only committed (or fresh) content remains above budget, the
planner surfaces a ``still_over_budget=True`` blocked plan and
``execute_eviction`` emits one loud WARN."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from archive_agent.config import Config
from archive_agent.librarian.eviction import execute_eviction, plan_eviction
from archive_agent.logging import configure_logging

_NOW = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _touch(path: Path, *, days_ago: int) -> None:
    ts = (_NOW - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def _seed(parent: Path, name: str, size: int, *, days_ago: int) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"\x00" * size)
    _touch(folder / "video.mp4", days_ago=days_ago)
    _touch(folder, days_ago=days_ago)
    return folder


def test_plan_flags_still_over_when_only_committed_content(
    db: sqlite3.Connection, config: Config
) -> None:
    config.librarian.max_disk_gb = 1
    _seed(
        config.paths.media_tv,
        "Committed Show",
        int(1.5 * 1_000_000_000),
        days_ago=365,
    )
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.still_over_budget is True
    assert plan.items == []
    assert plan.blocked_reason is not None
    assert "committed /media/tv" in plan.blocked_reason


def test_execute_emits_warn_on_blocked_plan(
    db: sqlite3.Connection, config: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    config.librarian.max_disk_gb = 1
    _seed(
        config.paths.media_tv,
        "Committed Show",
        int(1.5 * 1_000_000_000),
        days_ago=365,
    )
    # structlog routes through stdlib logging; configure_logging
    # force-replaces the stream handler so capsys can pick it up.
    configure_logging(level="INFO", fmt="console")
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.still_over_budget is True

    result = execute_eviction(plan, db, dry_run=True)
    assert result.still_over_budget is True

    err = capsys.readouterr().err
    assert "eviction_blocked" in err


def test_execute_warn_fires_even_on_dry_run(
    db: sqlite3.Connection, config: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """The warning is a state signal, not a side effect of deletion,
    so --dry-run still emits it."""
    config.librarian.max_disk_gb = 1
    _seed(
        config.paths.media_tv,
        "Committed Show",
        int(1.5 * 1_000_000_000),
        days_ago=365,
    )
    configure_logging(level="INFO", fmt="console")
    plan = plan_eviction(db, config, now=_NOW)
    execute_eviction(plan, db, dry_run=True)
    err = capsys.readouterr().err
    assert "eviction_blocked" in err
