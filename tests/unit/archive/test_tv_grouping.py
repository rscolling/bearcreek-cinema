"""TV grouping: marker parsing + classification ladder."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from archive_agent.archive.tv_grouping import (
    SxEy,
    classify_episode,
    group_unassigned_episodes,
    parse_episode_marker,
)
from archive_agent.metadata.models import TmdbShow
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


# --- parse_episode_marker ------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Dick Van Dyke Show S01E03 - Sick Boy", SxEy(1, 3, "The Dick Van Dyke Show")),
        ("William Tell s01e37 The Spider", SxEy(1, 37, "William Tell")),
        ("I Love Lucy S1E3", SxEy(1, 3, "I Love Lucy")),
        ("Twilight Zone 1x03", SxEy(1, 3, "Twilight Zone")),
        ("The Beverly Hillbillies 01x12", SxEy(1, 12, "The Beverly Hillbillies")),
        (
            "Gunsmoke Season 1 Episode 5 - Hack Prine",
            SxEy(1, 5, "Gunsmoke"),
        ),
        (
            "Bonanza Season 02 Episode 14",
            SxEy(2, 14, "Bonanza"),
        ),
        ("The Honeymooners - Ep 03 - The Golfer", SxEy(1, 3, "The Honeymooners")),
        ("My Little Margie Episode 7", SxEy(1, 7, "My Little Margie")),
        ("Father Knows Best episode 12 - The Vase", SxEy(1, 12, "Father Knows Best")),
    ],
)
def test_episode_marker_patterns(title: str, expected: SxEy) -> None:
    got = parse_episode_marker(title)
    assert got == expected


def test_parse_returns_none_on_plain_title() -> None:
    assert parse_episode_marker("A Random Movie Title") is None


def test_parse_handles_empty() -> None:
    assert parse_episode_marker("") is None


def test_parse_with_no_prefix() -> None:
    """Marker at position 0 — the prefix is empty."""
    got = parse_episode_marker("S01E03")
    assert got is not None
    assert got.title_prefix == ""


# --- classify_episode ---------------------------------------------------


def _candidate(
    archive_id: str,
    title: str,
    *,
    show_id: str | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.EPISODE,
        title=title,
        source_collection="television",
        status=CandidateStatus.NEW,
        discovered_at=datetime.now(UTC),
        show_id=show_id,
    )


def _show(tmdb_id: int, name: str = "Test Show") -> TmdbShow:
    return TmdbShow(id=tmdb_id, name=name)


async def test_already_grouped_short_circuits() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock()  # should never be called
    cand = _candidate("x", "Whatever", show_id="1433")
    cand = cand.model_copy(update={"season": 2, "episode": 5})
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "high"
    assert match.show_id == "1433"
    assert match.season == 2
    assert match.episode == 5
    tmdb.search_shows.assert_not_called()


async def test_high_confidence_with_marker_and_tmdb_hit() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[_show(1433, "The Dick Van Dyke Show")])
    cand = _candidate("ep1", "The Dick Van Dyke Show S01E03 - Sick Boy")
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "high"
    assert match.show_id == "1433"
    assert match.season == 1
    assert match.episode == 3
    # Searches with the prefix, not the raw title
    tmdb.search_shows.assert_called_once()
    args, _kwargs = tmdb.search_shows.call_args
    assert args[0] == "The Dick Van Dyke Show"


async def test_medium_when_single_match_without_marker() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[_show(1433, "The Dick Van Dyke Show")])
    cand = _candidate("ep2", "The Dick Van Dyke Show")
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "medium"
    assert match.show_id == "1433"
    assert match.season is None
    assert match.episode is None


async def test_low_when_multiple_matches_without_marker() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(
        return_value=[_show(1, "Foo"), _show(2, "Foo (1981)"), _show(3, "Foo 2")]
    )
    cand = _candidate("ep3", "Foo")
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "low"
    assert match.show_id == "1"  # top match recorded as suggestion


async def test_none_when_tmdb_empty() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[])
    cand = _candidate("ep4", "Something Extremely Obscure")
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "none"
    assert match.show_id is None


async def test_none_when_title_empty_after_marker_strip() -> None:
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock()
    cand = _candidate("ep5", "S01E03")  # just the marker, no show name
    match = await classify_episode(cand, tmdb)
    assert match.confidence == "none"
    tmdb.search_shows.assert_not_called()


# --- group_unassigned_episodes + review queue ---------------------------


async def test_high_match_writes_show_to_candidate(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("ep-high", "The Dick Van Dyke Show S01E03"))
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[_show(1433, "The Dick Van Dyke Show")])
    result = await group_unassigned_episodes(db, tmdb)
    assert result.high == 1
    assert result.classified == 1

    after = q_candidates.get_by_archive_id(db, "ep-high")
    assert after is not None
    assert after.show_id == "1433"
    assert after.season == 1
    assert after.episode == 3


async def test_low_match_goes_to_review_queue(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("ep-low", "Foo"))
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[_show(1, "Foo"), _show(2, "Foo (1981)")])
    result = await group_unassigned_episodes(db, tmdb)
    assert result.low == 1

    # Candidate's show_id stays unset (low confidence → don't force-assign)
    after = q_candidates.get_by_archive_id(db, "ep-low")
    assert after is not None
    assert after.show_id is None

    review = db.execute(
        "SELECT archive_id, confidence, suggested_show_id FROM tv_grouping_review"
    ).fetchone()
    assert review["archive_id"] == "ep-low"
    assert review["confidence"] == "low"
    assert review["suggested_show_id"] == "1"


async def test_none_match_goes_to_review_queue(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("ep-none", "Obscure Title"))
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[])
    result = await group_unassigned_episodes(db, tmdb)
    assert result.none_ == 1

    review = db.execute("SELECT confidence, suggested_show_id FROM tv_grouping_review").fetchone()
    assert review["confidence"] == "none"
    assert review["suggested_show_id"] is None


async def test_rerun_does_not_reclassify_grouped(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("ep-once", "DVDS S01E03"))
    tmdb = AsyncMock()
    tmdb.search_shows = AsyncMock(return_value=[_show(1433, "DVDS")])

    first = await group_unassigned_episodes(db, tmdb)
    assert first.classified == 1
    assert first.high == 1

    # Second run — the candidate now has show_id set, so it's filtered out
    # by the SELECT (show_id IS NULL).
    second = await group_unassigned_episodes(db, tmdb)
    assert second.classified == 0


async def test_model_dump_for_cli_renames_none(db: sqlite3.Connection) -> None:
    """Python's ``none`` is a builtin; we report it as ``none`` via the
    rename helper so CLI JSON is readable."""
    from archive_agent.archive.tv_grouping import GroupingResult

    r = GroupingResult(classified=4, high=1, medium=1, low=1, none_=1)
    d = r.model_dump_for_cli()
    assert "none" in d
    assert "none_" not in d
    assert d["none"] == 1
