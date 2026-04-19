"""promote_movie + promote_show — round-trip from staging zones
to user-owned zones."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from archive_agent.config import Config
from archive_agent.librarian import PlacementError, promote_movie, promote_show
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates


def _movie(
    archive_id: str = "sita_sings",
    *,
    title: str = "Sita Sings the Blues",
    year: int = 2008,
    status: CandidateStatus = CandidateStatus.DOWNLOADED,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.MOVIE,
        title=title,
        year=year,
        source_collection="moviesandfilms",
        status=status,
        discovered_at=datetime.now(UTC),
    )


def _show_episode(
    archive_id: str = "dvds-s01e03",
    *,
    show_id: str = "1433",
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.EPISODE,
        title="Sick Boy and Sore Loser",
        year=1961,
        show_id=show_id,
        season=1,
        episode=3,
        source_collection="television",
        status=CandidateStatus.SAMPLING,
        discovered_at=datetime.now(UTC),
    )


def _seed_movie_folder(rec_root: Path, title: str, year: int) -> Path:
    folder = rec_root / f"{title} ({year})"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{title} ({year}).mp4").write_bytes(b"\x00" * 1000)
    return folder


# --- movies --------------------------------------------------------------


def test_promote_movie_moves_folder(db: sqlite3.Connection, config: Config) -> None:
    _seed_movie_folder(config.paths.media_recommendations, "Sita Sings the Blues", 2008)
    cand = _movie()
    q_candidates.upsert_candidate(db, cand)

    result = promote_movie(db, config, cand)
    assert result.moved is True
    # Source gone, dest exists
    assert not (config.paths.media_recommendations / "Sita Sings the Blues (2008)").exists()
    moved = config.paths.media_movies / "Sita Sings the Blues (2008)"
    assert moved.exists()
    assert (moved / "Sita Sings the Blues (2008).mp4").exists()

    row = q_candidates.get_by_archive_id(db, "sita_sings")
    assert row is not None
    assert row.status == CandidateStatus.COMMITTED

    audit = db.execute("SELECT action, zone, archive_id FROM librarian_actions").fetchone()
    assert audit["action"] == "promote"
    assert audit["zone"] == "movies"


def test_promote_movie_missing_source(db: sqlite3.Connection, config: Config) -> None:
    cand = _movie()
    q_candidates.upsert_candidate(db, cand)
    with pytest.raises(PlacementError, match="source folder missing"):
        promote_movie(db, config, cand)


def test_promote_movie_dry_run_writes_nothing(db: sqlite3.Connection, config: Config) -> None:
    src_folder = _seed_movie_folder(
        config.paths.media_recommendations, "Sita Sings the Blues", 2008
    )
    cand = _movie()
    q_candidates.upsert_candidate(db, cand)

    result = promote_movie(db, config, cand, dry_run=True)
    assert result.moved is False
    assert src_folder.exists()
    assert not (config.paths.media_movies / "Sita Sings the Blues (2008)").exists()
    row = q_candidates.get_by_archive_id(db, "sita_sings")
    assert row is not None
    assert row.status == CandidateStatus.DOWNLOADED  # unchanged
    count = db.execute("SELECT COUNT(*) FROM librarian_actions").fetchone()[0]
    assert count == 0


def test_promote_movie_disambiguates_when_dest_exists(
    db: sqlite3.Connection, config: Config
) -> None:
    _seed_movie_folder(config.paths.media_recommendations, "Sita Sings the Blues", 2008)
    # Pre-existing movie folder with the same name (user already owned it)
    (config.paths.media_movies / "Sita Sings the Blues (2008)").mkdir(parents=True)

    result = promote_movie(db, config, _movie())
    # New folder appended with (1)
    assert result.dest_path.name == "Sita Sings the Blues (2008) (1)"
    assert result.dest_path.exists()


# --- shows ---------------------------------------------------------------


def test_promote_show_moves_sampler_tree(db: sqlite3.Connection, config: Config) -> None:
    show_folder = config.paths.media_tv_sampler / "The Dick Van Dyke Show"
    season_folder = show_folder / "Season 01"
    season_folder.mkdir(parents=True)
    (season_folder / "ep1.mp4").write_bytes(b"\x00" * 500)
    (season_folder / "ep2.mp4").write_bytes(b"\x00" * 500)

    cand = _show_episode()
    q_candidates.upsert_candidate(db, cand)

    result = promote_show(db, config, cand, show_title="The Dick Van Dyke Show")
    assert result.moved is True
    assert not show_folder.exists()
    moved = config.paths.media_tv / "The Dick Van Dyke Show"
    assert moved.exists()
    assert (moved / "Season 01" / "ep1.mp4").exists()
    assert (moved / "Season 01" / "ep2.mp4").exists()
    assert result.size_bytes == 1000

    row = q_candidates.get_by_archive_id(db, "dvds-s01e03")
    assert row is not None
    assert row.status == CandidateStatus.COMMITTED


def test_promote_show_falls_back_to_show_id_folder(db: sqlite3.Connection, config: Config) -> None:
    """Without ``show_title``, folder name derives from show_id
    (TMDb numeric id as string)."""
    (config.paths.media_tv_sampler / "1433").mkdir(parents=True)
    (config.paths.media_tv_sampler / "1433" / "ep.mp4").write_bytes(b"x")

    cand = _show_episode()
    q_candidates.upsert_candidate(db, cand)

    result = promote_show(db, config, cand)
    assert result.moved is True
    assert (config.paths.media_tv / "1433" / "ep.mp4").exists()
