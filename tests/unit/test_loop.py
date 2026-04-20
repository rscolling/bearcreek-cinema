"""Daemon scheduler: dispatch, interval gating, error isolation, shutdown.

The scheduler is pure orchestration; real task handlers are covered by
their own modules' tests. Here we assert on:

- A task whose interval has elapsed runs; one whose hasn't, doesn't.
- A task that raises doesn't take down the loop; ``last_error`` gets
  set and ``last_run`` still advances.
- ``stop()`` breaks the ``run()`` loop between ticks.
- ``run_one_shot`` fires every task once regardless of interval.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from archive_agent.config import (
    ApiConfig,
    ArchiveConfig,
    Config,
    JellyfinConfig,
    LibrarianConfig,
    LlmClaudeConfig,
    LlmConfig,
    LlmOllamaConfig,
    LlmWorkflowsConfig,
    LoggingConfig,
    PathsConfig,
    RecommendConfig,
    TasteConfig,
    TmdbConfig,
)
from archive_agent.loop import (
    Daemon,
    DaemonContext,
    TaskSpec,
    build_default_tasks,
    run_one_shot,
)
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.db import connect


def _minimal_config(tmp: Path) -> Config:
    return Config(
        paths=PathsConfig(
            state_db=tmp / "state.db",
            media_movies=tmp / "movies",
            media_tv=tmp / "tv",
            media_recommendations=tmp / "rec",
            media_tv_sampler=tmp / "sampler",
        ),
        jellyfin=JellyfinConfig(
            url="http://localhost:8096",
            api_key=SecretStr("k"),
            user_id="u",
        ),
        archive=ArchiveConfig(),
        tmdb=TmdbConfig(api_key=SecretStr("t")),
        llm=LlmConfig(
            workflows=LlmWorkflowsConfig(),
            ollama=LlmOllamaConfig(),
            claude=LlmClaudeConfig(),
        ),
        librarian=LibrarianConfig(),
        taste=TasteConfig(),
        recommend=RecommendConfig(),
        api=ApiConfig(),
        logging=LoggingConfig(),
    )


class _FakeProvider:
    name = "tfidf"

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")

    async def rank(self, *a: Any, **k: Any) -> list[Any]:
        return []

    async def update_profile(self, *a: Any, **k: Any) -> Any:
        raise NotImplementedError

    async def parse_search(self, *a: Any, **k: Any) -> Any:
        raise NotImplementedError


@pytest.fixture
def ctx(tmp_path: Path) -> DaemonContext:
    cfg = _minimal_config(tmp_path)
    conn = connect(":memory:")
    return DaemonContext(config=cfg, conn=conn, provider=_FakeProvider())


# --- run_once: interval gating ------------------------------------------


async def test_run_once_fires_due_tasks(ctx: DaemonContext) -> None:
    calls: list[str] = []

    async def _a(_: DaemonContext) -> None:
        calls.append("a")

    async def _b(_: DaemonContext) -> None:
        calls.append("b")

    daemon = Daemon(ctx)
    daemon.register(TaskSpec(name="a", interval=timedelta(seconds=1), handler=_a))
    daemon.register(TaskSpec(name="b", interval=timedelta(seconds=1), handler=_b))

    fired = await daemon.run_once()

    assert fired == ["a", "b"]
    assert calls == ["a", "b"]


async def test_run_once_skips_task_within_its_interval(ctx: DaemonContext) -> None:
    calls: list[str] = []

    async def _a(_: DaemonContext) -> None:
        calls.append("a")

    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    task = TaskSpec(
        name="a",
        interval=timedelta(minutes=10),
        handler=_a,
        last_run=now - timedelta(minutes=2),
    )
    daemon = Daemon(ctx)
    daemon.register(task)

    fired = await daemon.run_once(now=now)

    assert fired == []
    assert calls == []


async def test_run_once_respects_enabled_false(ctx: DaemonContext) -> None:
    calls: list[str] = []

    async def _a(_: DaemonContext) -> None:
        calls.append("a")

    daemon = Daemon(ctx)
    daemon.register(TaskSpec(name="a", interval=timedelta(seconds=1), handler=_a, enabled=False))

    fired = await daemon.run_once()

    assert fired == []
    assert calls == []


# --- error isolation ----------------------------------------------------


async def test_failing_task_does_not_block_others(ctx: DaemonContext) -> None:
    calls: list[str] = []

    async def _boom(_: DaemonContext) -> None:
        raise RuntimeError("kaboom")

    async def _ok(_: DaemonContext) -> None:
        calls.append("ok")

    daemon = Daemon(ctx)
    boom_task = TaskSpec(name="boom", interval=timedelta(seconds=1), handler=_boom)
    daemon.register(boom_task)
    daemon.register(TaskSpec(name="ok", interval=timedelta(seconds=1), handler=_ok))

    fired = await daemon.run_once()

    assert fired == ["boom", "ok"]  # both "ran" from the scheduler's POV
    assert calls == ["ok"]
    assert boom_task.last_error is not None
    assert "kaboom" in boom_task.last_error
    assert boom_task.last_run is not None  # stamped even on error


async def test_last_error_cleared_on_subsequent_success(ctx: DaemonContext) -> None:
    state = {"calls": 0}

    async def _flaky(_: DaemonContext) -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("first call fails")

    daemon = Daemon(ctx)
    task = TaskSpec(name="flaky", interval=timedelta(seconds=0), handler=_flaky)
    daemon.register(task)

    await daemon.run_once()
    assert task.last_error is not None

    await daemon.run_once()
    assert task.last_error is None


# --- run() loop lifecycle -----------------------------------------------


async def test_run_stops_when_stop_called(ctx: DaemonContext) -> None:
    call_count = {"n": 0}

    async def _counter(_: DaemonContext) -> None:
        call_count["n"] += 1

    daemon = Daemon(ctx, tick_seconds=0.05)
    daemon.register(TaskSpec(name="counter", interval=timedelta(seconds=0), handler=_counter))

    async def _stopper() -> None:
        await asyncio.sleep(0.15)
        daemon.stop()

    await asyncio.gather(daemon.run(), _stopper())

    # Should have ticked at least twice (t=0 and t=~0.05), possibly more.
    assert call_count["n"] >= 2


# --- run_one_shot -------------------------------------------------------


async def test_run_one_shot_fires_every_default_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace every task's handler with a no-op so we don't hit real
    Jellyfin / Archive.org / TMDb / Ollama during the test."""
    cfg = _minimal_config(tmp_path)
    conn = connect(":memory:")

    from archive_agent.state.migrations import apply_pending

    apply_pending(conn)

    # Patch every build_default_tasks handler to a no-op via monkeypatch
    # on the ``loop`` module's task_* functions.
    import archive_agent.loop as loop_mod

    async def _noop(_: DaemonContext) -> None:
        return None

    for name in (
        "task_aggregate",
        "task_history_sync",
        "task_discover",
        "task_enrich",
        "task_profile_update",
        "task_recommend",
        "task_evict",
    ):
        monkeypatch.setattr(loop_mod, name, _noop)

    fired = await run_one_shot(cfg, conn, _FakeProvider())

    # All 7 default tasks should have fired exactly once.
    assert sorted(fired) == sorted(
        [
            "aggregate",
            "history_sync",
            "discover",
            "enrich",
            "profile_update",
            "recommend",
            "evict",
        ]
    )


# --- build_default_tasks -----------------------------------------------


def test_default_tasks_read_intervals_from_config(tmp_path: Path) -> None:
    cfg = _minimal_config(tmp_path)
    cfg.taste.aggregate_interval_minutes = 7
    cfg.archive.discovery_interval_minutes = 45
    cfg.recommend.interval_hours = 3

    tasks = build_default_tasks(cfg)
    by_name = {t.name: t for t in tasks}

    assert by_name["aggregate"].interval == timedelta(minutes=7)
    assert by_name["discover"].interval == timedelta(minutes=45)
    assert by_name["history_sync"].interval == timedelta(minutes=45)
    assert by_name["recommend"].interval == timedelta(hours=3)


# --- suppress unused import warning --------------------------------------


_ = sqlite3
