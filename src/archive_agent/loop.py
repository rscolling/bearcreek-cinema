"""Async job loop for the archive-agent daemon.

Stitches the existing pipeline functions (discovery, enrichment,
history sync, show aggregator, profile update, recommend, eviction)
into one scheduler that ticks forever on don-quixote. The commands
themselves live in their existing modules — this file is glue, not
new behaviour.

## Resilience

One task failing never takes down the loop. Every handler runs in a
try/except that logs the exception and moves on; the next scheduled
firing gets a fresh attempt. A task that raises is treated exactly
the same as a task that succeeded with zero work — its ``last_run``
is stamped either way so a broken integration can't cause a hot
retry storm.

## Determinism

The scheduler's tick is deterministic: on each pass it checks every
task against ``now - last_run``, runs every task whose interval has
elapsed, then sleeps for at most ``tick_seconds`` (or until stopped).
This keeps the code trivial to unit-test; ``run_once()`` exposes the
same logic with a single synchronous pass for ``--one-shot`` mode.

## Shutdown

Top-level ``start()`` wires SIGINT + SIGTERM (on platforms that
support them — Windows doesn't signal SIGTERM the same way) to
``Daemon.stop()``. On ``stop()`` the outer loop exits between ticks;
tasks that are already mid-flight finish out naturally because
everything is ``await``-based and the scheduler waits on each
handler.
"""

from __future__ import annotations

import asyncio
import signal
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.ranking.provider import LLMProvider

_log = get_logger("archive_agent.loop")


# A handler is any coroutine that takes the daemon context and
# returns None. Exceptions are caught by the scheduler.
TaskHandler = Callable[["DaemonContext"], Awaitable[None]]


@dataclass
class DaemonContext:
    """Shared state threaded to every task handler."""

    config: Config
    conn: sqlite3.Connection
    provider: LLMProvider


@dataclass
class TaskSpec:
    name: str
    interval: timedelta
    handler: TaskHandler
    enabled: bool = True
    last_run: datetime | None = None
    last_error: str | None = None


class Daemon:
    """Minimal scheduler: wakes every ``tick_seconds``, fires due tasks."""

    def __init__(self, ctx: DaemonContext, *, tick_seconds: float = 30.0) -> None:
        self._ctx = ctx
        self._tick_seconds = tick_seconds
        self._tasks: list[TaskSpec] = []
        self._stop_event = asyncio.Event()

    # --- wiring -----------------------------------------------------

    def register(self, task: TaskSpec) -> None:
        self._tasks.append(task)

    @property
    def tasks(self) -> list[TaskSpec]:
        """Read-only view of registered tasks (for tests + CLI status)."""
        return list(self._tasks)

    # --- lifecycle --------------------------------------------------

    async def run(self) -> None:
        """Park on the stop event; on each wakeup run any due tasks."""
        _log.info("daemon_started", tasks=[t.name for t in self._tasks])
        while not self._stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                # Expected path — just finished a tick, loop again.
                continue
        _log.info("daemon_stopped")

    async def run_once(self, now: datetime | None = None) -> list[str]:
        """Fire every task whose interval has elapsed.

        Returns the names of the tasks that actually ran — useful for
        the ``--one-shot`` CLI path and for tests.
        """
        current = now or datetime.now(UTC)
        fired: list[str] = []
        for task in self._tasks:
            if not task.enabled:
                continue
            if task.last_run is not None and current - task.last_run < task.interval:
                continue
            await self._run_task(task)
            fired.append(task.name)
        return fired

    async def _run_task(self, task: TaskSpec) -> None:
        started = datetime.now(UTC)
        _log.info("task_start", name=task.name)
        try:
            await task.handler(self._ctx)
        except Exception as exc:
            task.last_error = f"{type(exc).__name__}: {exc}"
            _log.error(
                "task_error",
                name=task.name,
                error=type(exc).__name__,
                detail=str(exc),
            )
        else:
            task.last_error = None
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            _log.info("task_done", name=task.name, elapsed_ms=elapsed_ms)
        finally:
            task.last_run = datetime.now(UTC)

    def stop(self) -> None:
        """Signal the loop to exit at the next tick boundary."""
        self._stop_event.set()


# --- default task handlers -------------------------------------------
#
# Each handler catches its own "external service down" exceptions by
# leaning on the underlying modules' already-resilient behavior:
# ranking providers fall back to TF-IDF (ADR-002), aggregation is
# DB-only, downloads log + skip. The scheduler's outer try/except
# is a final safety net.


async def task_aggregate(ctx: DaemonContext) -> None:
    from archive_agent.taste import aggregate_all_shows

    events = aggregate_all_shows(ctx.conn, ctx.config.taste)
    _log.info("aggregate_result", emitted=len(events))


async def task_profile_update(ctx: DaemonContext) -> None:
    from archive_agent.taste import run_if_due

    result = await run_if_due(ctx.conn, ctx.provider, ctx.config.taste)
    version = result.version if result is not None else None
    _log.info("profile_update_result", new_version=version)


async def task_recommend(ctx: DaemonContext) -> None:
    from archive_agent.commands.recommend import NoProfileError, recommend

    try:
        result = await recommend(ctx.conn, ctx.config)
    except NoProfileError:
        _log.info("recommend_skipped", reason="no_profile_yet")
        return
    _log.info(
        "recommend_result",
        n=result.n_returned,
        batch=result.batch_id or None,
        provider=result.provider,
    )


async def task_history_sync(ctx: DaemonContext) -> None:
    from archive_agent.jellyfin.client import JellyfinClient
    from archive_agent.jellyfin.history import ingest_all_history

    async with JellyfinClient(
        ctx.config.jellyfin.url,
        ctx.config.jellyfin.api_key,
        ctx.config.jellyfin.user_id,
    ) as client:
        result = await ingest_all_history(client, ctx.conn)
    _log.info(
        "history_sync_result",
        movies=result.movie_events_inserted,
        episodes=result.episode_watches_inserted,
    )


async def task_discover(ctx: DaemonContext) -> None:
    from archive_agent.archive.discovery import discover

    result = await discover(ctx.conn, ctx.config, collection="both", limit=100)
    _log.info(
        "discover_result",
        inserted=result.inserted,
        updated=result.updated,
    )


async def task_enrich(ctx: DaemonContext) -> None:
    from archive_agent.metadata import TmdbClient, enrich_new_candidates

    async with TmdbClient(ctx.config.tmdb.api_key, ctx.conn) as client:
        result = await enrich_new_candidates(ctx.conn, client, limit=100)
    _log.info(
        "enrich_result",
        seen=result.seen,
        updated=result.updated,
        missing=result.missing_tmdb_match,
    )


async def task_evict(ctx: DaemonContext) -> None:
    from archive_agent.librarian import execute_eviction, plan_eviction

    plan = plan_eviction(ctx.conn, ctx.config)
    if not plan.items:
        _log.info("evict_skipped", reason="nothing_due")
        return
    result = execute_eviction(plan, ctx.conn)
    _log.info(
        "evict_result",
        evicted=result.evicted,
        freed_bytes=result.freed_bytes,
        still_over=result.still_over_budget,
    )


def build_default_tasks(config: Config) -> list[TaskSpec]:
    """Build the standard task set with intervals sourced from config."""
    return [
        # DB-only task — cheap, runs often.
        TaskSpec(
            name="aggregate",
            interval=timedelta(minutes=config.taste.aggregate_interval_minutes),
            handler=task_aggregate,
        ),
        # Hits Jellyfin — same cadence as discovery so both sides of
        # the taste pipeline stay roughly in sync.
        TaskSpec(
            name="history_sync",
            interval=timedelta(minutes=config.archive.discovery_interval_minutes),
            handler=task_history_sync,
        ),
        # Hits Archive.org.
        TaskSpec(
            name="discover",
            interval=timedelta(minutes=config.archive.discovery_interval_minutes),
            handler=task_discover,
        ),
        # Hits TMDb — enriches whatever discovery just pulled in.
        TaskSpec(
            name="enrich",
            interval=timedelta(minutes=config.archive.discovery_interval_minutes),
            handler=task_enrich,
        ),
        # LLM call; plan_update rate-limits internally — we only
        # check hourly whether it's time.
        TaskSpec(
            name="profile_update",
            interval=timedelta(hours=1),
            handler=task_profile_update,
        ),
        # LLM call; produces a fresh batch.
        TaskSpec(
            name="recommend",
            interval=timedelta(hours=config.recommend.interval_hours),
            handler=task_recommend,
        ),
        # Disk hygiene.
        TaskSpec(
            name="evict",
            interval=timedelta(hours=1),
            handler=task_evict,
        ),
    ]


# --- top-level entry point -------------------------------------------


def _install_signal_handlers(daemon: Daemon) -> None:
    """Best-effort SIGINT/SIGTERM wiring; harmless on platforms without signals."""
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, daemon.stop)
        except (NotImplementedError, RuntimeError):
            # Windows + some event loop configurations can't register
            # Unix signals. Ctrl+C still works via KeyboardInterrupt.
            continue


async def start(
    config: Config,
    conn: sqlite3.Connection,
    provider: LLMProvider,
    *,
    tick_seconds: float = 30.0,
    extra_tasks: list[TaskSpec] | None = None,
) -> None:
    """Build the daemon with the default tasks and run until stopped."""
    ctx = DaemonContext(config=config, conn=conn, provider=provider)
    daemon = Daemon(ctx, tick_seconds=tick_seconds)
    for task in build_default_tasks(config):
        daemon.register(task)
    for task in extra_tasks or []:
        daemon.register(task)

    _install_signal_handlers(daemon)
    try:
        await daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


async def run_one_shot(
    config: Config,
    conn: sqlite3.Connection,
    provider: LLMProvider,
) -> list[str]:
    """Run each task once, regardless of its last-run timestamp.

    Used by ``archive-agent daemon --one-shot`` so ops can manually
    kick a single pass when debugging on don-quixote.
    """
    ctx = DaemonContext(config=config, conn=conn, provider=provider)
    daemon = Daemon(ctx)
    for task in build_default_tasks(config):
        task.last_run = None
        daemon.register(task)
    return await daemon.run_once()


__all__ = [
    "Daemon",
    "DaemonContext",
    "TaskHandler",
    "TaskSpec",
    "build_default_tasks",
    "run_one_shot",
    "start",
    "task_aggregate",
    "task_discover",
    "task_enrich",
    "task_evict",
    "task_history_sync",
    "task_profile_update",
    "task_recommend",
]
