"""place() — movies + episodes, budget guard, dry_run, disambiguation."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from archive_agent.config import Config
from archive_agent.librarian import (
    BudgetExceededError,
    PlacementError,
    Zone,
    place,
)
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates


def _candidate(
    *,
    archive_id: str = "sita_sings",
    content_type: ContentType = ContentType.MOVIE,
    title: str = "Sita Sings the Blues",
    year: int | None = 2008,
    show_id: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        show_id=show_id,
        season=season,
        episode=episode,
        source_collection=("moviesandfilms" if content_type == ContentType.MOVIE else "television"),
        status=CandidateStatus.NEW,
        discovered_at=datetime.now(UTC),
    )


def _write(path: Path, size: int = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


# --- happy paths ---------------------------------------------------------


def test_place_movie_into_recommendations(db: sqlite3.Connection, config: Config) -> None:
    source = _write(config.paths.state_db.parent / "staging" / "sita.mp4", 500)
    result = place(
        db,
        config,
        candidate=_candidate(),
        source_path=source,
        zone=Zone.RECOMMENDATIONS,
    )
    assert result.moved is True
    assert result.dest_path.exists()
    assert result.dest_path.name == "Sita Sings the Blues (2008).mp4"
    assert result.dest_path.parent.name == "Sita Sings the Blues (2008)"
    assert result.dest_path.parent.parent == config.paths.media_recommendations
    # Source got moved — gone
    assert not source.exists()

    row = q_candidates.get_by_archive_id(db, "sita_sings")
    assert row is not None
    assert row.status == CandidateStatus.DOWNLOADED

    audit = db.execute(
        "SELECT action, zone, archive_id, size_bytes FROM librarian_actions"
    ).fetchone()
    assert audit["action"] == "download"
    assert audit["zone"] == "recommendations"
    assert audit["archive_id"] == "sita_sings"
    assert audit["size_bytes"] == 500


def test_place_episode_into_sampler(db: sqlite3.Connection, config: Config) -> None:
    source = _write(config.paths.state_db.parent / "staging" / "dvds_s01e03.mp4", 500)
    cand = _candidate(
        archive_id="dvds-s01e03",
        content_type=ContentType.EPISODE,
        title="Sick Boy and Sore Loser",
        year=1961,
        show_id="1433",
        season=1,
        episode=3,
    )
    result = place(
        db,
        config,
        candidate=cand,
        source_path=source,
        zone=Zone.TV_SAMPLER,
        show_title="The Dick Van Dyke Show",
    )
    assert result.moved is True
    assert result.dest_path.name == (
        "The Dick Van Dyke Show - S01E03 - Sick Boy and Sore Loser.mp4"
    )
    assert result.dest_path.parent.name == "Season 01"
    assert result.dest_path.parent.parent.name == "The Dick Van Dyke Show"

    row = q_candidates.get_by_archive_id(db, "dvds-s01e03")
    assert row is not None
    assert row.status == CandidateStatus.SAMPLING


# --- guards --------------------------------------------------------------


def test_place_rejects_user_owned_zone(db: sqlite3.Connection, config: Config) -> None:
    source = _write(config.paths.state_db.parent / "staging" / "x.mp4", 100)
    with pytest.raises(PlacementError, match="USER_OWNED"):
        place(
            db,
            config,
            candidate=_candidate(),
            source_path=source,
            zone=Zone.MOVIES,
        )
    assert source.exists()  # no move happened


def test_place_rejects_over_budget(db: sqlite3.Connection, config: Config) -> None:
    # config's max_disk_gb=10 (from the conftest fixture). Write a 12 GB
    # "source" file by seeding the tv zone with enough content to eat
    # the budget, then try placing one more byte.
    (config.paths.media_tv / "huge.mp4").write_bytes(b"\x00" * (11 * 1_000_000_000))
    source = _write(config.paths.state_db.parent / "staging" / "small.mp4", 10)
    with pytest.raises(BudgetExceededError, match="would push agent usage"):
        place(
            db,
            config,
            candidate=_candidate(),
            source_path=source,
            zone=Zone.RECOMMENDATIONS,
        )
    assert source.exists()


def test_place_missing_source_raises(db: sqlite3.Connection, config: Config) -> None:
    missing = config.paths.state_db.parent / "does_not_exist.mp4"
    with pytest.raises(PlacementError, match="does not exist"):
        place(
            db,
            config,
            candidate=_candidate(),
            source_path=missing,
            zone=Zone.RECOMMENDATIONS,
        )


# --- dry_run + disambiguation -------------------------------------------


def test_place_dry_run_writes_nothing(db: sqlite3.Connection, config: Config) -> None:
    source = _write(config.paths.state_db.parent / "staging" / "sita.mp4", 100)
    result = place(
        db,
        config,
        candidate=_candidate(),
        source_path=source,
        zone=Zone.RECOMMENDATIONS,
        dry_run=True,
    )
    assert result.moved is False
    assert source.exists()  # source preserved
    assert not result.dest_path.exists()  # dest not created
    # No audit row, no status update
    count = db.execute("SELECT COUNT(*) FROM librarian_actions").fetchone()[0]
    assert count == 0


def test_place_disambiguates_collision(db: sqlite3.Connection, config: Config) -> None:
    # Pre-create a file at the expected dest path
    folder = config.paths.media_recommendations / "Sita Sings the Blues (2008)"
    folder.mkdir(parents=True)
    (folder / "Sita Sings the Blues (2008).mp4").write_bytes(b"old")

    source = _write(config.paths.state_db.parent / "staging" / "sita.mp4", 500)
    result = place(
        db,
        config,
        candidate=_candidate(),
        source_path=source,
        zone=Zone.RECOMMENDATIONS,
    )
    assert result.dest_path.name == "Sita Sings the Blues (2008) (1).mp4"
    assert (folder / "Sita Sings the Blues (2008).mp4").read_bytes() == b"old"
    assert result.dest_path.exists()
