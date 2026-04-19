"""Eviction planning: TTL cutoffs, oldest-first ordering, stop-when-under-budget, never-touch-movies."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from archive_agent.config import Config
from archive_agent.librarian.eviction import (
    _collect_zone_items,
    _find_candidate_for_folder,
    last_touched_at,
    plan_eviction,
)
from archive_agent.librarian.zones import Zone

_NOW = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _touch(path: Path, *, days_ago: int) -> None:
    """Set ``path``'s mtime to ``_NOW - days_ago``."""
    ts = (_NOW - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def _seed_folder(parent: Path, name: str, size_bytes: int, *, days_ago: int) -> Path:
    """Create a single-file folder under ``parent``. Returns the folder path."""
    folder = parent / name
    folder.mkdir(parents=True)
    file = folder / "video.mp4"
    file.write_bytes(b"\x00" * size_bytes)
    _touch(file, days_ago=days_ago)
    _touch(folder, days_ago=days_ago)
    return folder


# --- plan_eviction at the happy paths ---


def test_nothing_planned_when_under_budget(db: sqlite3.Connection, config: Config) -> None:
    # Budget 10 GB (from fixture); zones empty — over_budget=False
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.would_free_bytes == 0
    assert plan.items == []
    assert plan.still_over_budget is False


def test_stale_recommendations_are_planned(db: sqlite3.Connection, config: Config) -> None:
    # Force over-budget
    config.librarian.max_disk_gb = 1
    # Write 1.5 GB into recommendations, all stale
    _seed_folder(
        config.paths.media_recommendations,
        "Stale Movie (1950)",
        int(1.5 * 1_000_000_000),
        days_ago=40,
    )
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.would_free_bytes > 0
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.zone == Zone.RECOMMENDATIONS
    assert item.reason == "recommendation_untouched"
    assert plan.still_over_budget is False


def test_fresh_items_are_not_planned(db: sqlite3.Connection, config: Config) -> None:
    config.librarian.max_disk_gb = 1
    # 1.5 GB of recent content → still over budget, but none past TTL
    _seed_folder(
        config.paths.media_recommendations,
        "Fresh Movie (2024)",
        int(1.5 * 1_000_000_000),
        days_ago=3,
    )
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.items == []  # everything is within 14-day TTL
    assert plan.still_over_budget is True
    assert plan.blocked_reason is not None


def test_plan_picks_oldest_first_and_stops_at_budget(
    db: sqlite3.Connection, config: Config
) -> None:
    """Three stale folders; only the oldest two are needed to free
    enough bytes to meet the overage."""
    config.librarian.max_disk_gb = 1
    # 1.8 GB total (600 MB x 3) -> overage 0.8 GB
    _seed_folder(
        config.paths.media_recommendations,
        "Oldest",
        600_000_000,
        days_ago=90,
    )
    _seed_folder(
        config.paths.media_recommendations,
        "Middle",
        600_000_000,
        days_ago=60,
    )
    _seed_folder(
        config.paths.media_recommendations,
        "Newest Stale",
        600_000_000,
        days_ago=20,
    )
    plan = plan_eviction(db, config, now=_NOW)
    # Overage is 800 MB, oldest item (600 MB) alone isn't enough, so
    # middle also gets picked — but not Newest.
    assert len(plan.items) == 2
    names = [p.path.name for p in plan.items]
    assert names == ["Oldest", "Middle"]
    assert plan.would_free_bytes == 1_200_000_000
    assert plan.still_over_budget is False


def test_sampler_ttl_differs_from_recommendations(db: sqlite3.Connection, config: Config) -> None:
    config.librarian.max_disk_gb = 1
    # 15 days: stale for recommendations (14d), NOT stale for sampler (30d)
    _seed_folder(
        config.paths.media_tv_sampler,
        "Borderline Show",
        int(1.5 * 1_000_000_000),
        days_ago=15,
    )
    plan = plan_eviction(db, config, now=_NOW)
    assert plan.items == []
    assert plan.still_over_budget is True


def test_sampler_zone_respected_at_30d(db: sqlite3.Connection, config: Config) -> None:
    config.librarian.max_disk_gb = 1
    _seed_folder(
        config.paths.media_tv_sampler,
        "Stale Sampler",
        int(1.5 * 1_000_000_000),
        days_ago=35,
    )
    plan = plan_eviction(db, config, now=_NOW)
    assert len(plan.items) == 1
    assert plan.items[0].reason == "sampler_untouched"


def test_movies_zone_never_in_plan(db: sqlite3.Connection, config: Config) -> None:
    """Hard guardrail: /media/movies is user-owned — even very stale
    content there must not end up in the plan."""
    config.librarian.max_disk_gb = 1
    # Put 5 GB of stale content in /media/movies
    _seed_folder(
        config.paths.media_movies,
        "Classic Film (1940)",
        5_000_000_000,
        days_ago=365,
    )
    plan = plan_eviction(db, config, now=_NOW)
    # /media/movies is user-owned and not counted toward the budget,
    # so budget_report.over_budget is False and the plan is empty.
    assert plan.items == []


def test_blocked_reason_mentions_committed_tv(db: sqlite3.Connection, config: Config) -> None:
    config.librarian.max_disk_gb = 1
    _seed_folder(
        config.paths.media_tv,
        "Committed Show",
        int(1.5 * 1_000_000_000),
        days_ago=365,
    )
    plan = plan_eviction(db, config, now=_NOW)
    # /media/tv IS agent-managed so over_budget fires, but we never
    # evict from there without a proposal. Plan is empty + blocked.
    assert plan.items == []
    assert plan.still_over_budget is True
    assert plan.blocked_reason is not None
    assert "committed /media/tv" in plan.blocked_reason


# --- helper unit tests ---


def test_last_touched_at_floors_on_discovered_at(db: sqlite3.Connection) -> None:
    from archive_agent.state.models import Candidate, CandidateStatus, ContentType

    c = Candidate(
        archive_id="x",
        content_type=ContentType.MOVIE,
        title="Movie",
        source_collection="moviesandfilms",
        status=CandidateStatus.NEW,
        discovered_at=_NOW - timedelta(days=5),
    )
    # No folder provided → only candidate signal matters.
    got = last_touched_at(db, c, folder=None)
    assert got == c.discovered_at


def test_last_touched_at_prefers_folder_mtime_when_newer(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    from archive_agent.state.models import Candidate, CandidateStatus, ContentType

    c = Candidate(
        archive_id="x",
        content_type=ContentType.MOVIE,
        title="Movie",
        source_collection="moviesandfilms",
        status=CandidateStatus.NEW,
        discovered_at=_NOW - timedelta(days=30),
    )
    folder = _seed_folder(tmp_path, "Recent", 100, days_ago=3)
    got = last_touched_at(db, c, folder=folder)
    # Should be ~3 days ago, not 30.
    delta = _NOW - got
    assert timedelta(days=2) < delta < timedelta(days=4)


def test_collect_zone_items_skips_files(
    db: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    # A loose file (not a folder) in recommendations should be ignored
    # by the planner — the layout is always Title (Year)/file.mp4
    loose = config.paths.media_recommendations / "loose.mp4"
    loose.write_bytes(b"x")
    _touch(loose, days_ago=40)
    items = _collect_zone_items(
        db,
        config,
        Zone.RECOMMENDATIONS,
        timedelta(days=14),
        "recommendation_untouched",
        _NOW,
    )
    assert items == []


def test_find_candidate_returns_none_when_no_download_match(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    folder = tmp_path / "no-match"
    folder.mkdir()
    assert _find_candidate_for_folder(db, folder) is None
