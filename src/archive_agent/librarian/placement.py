"""File placement into /media zones + promote lifecycle.

GUARDRAILS: this is the **only** module in the agent that calls
``shutil.move`` under ``/media/*``. Every other module asks for
placement through here. Direct writes into USER_OWNED zones
(``/media/movies``) are rejected; promotion from RECOMMENDATIONS →
MOVIES happens only through ``promote_movie`` which tracks the
``librarian_actions`` audit trail.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archive_agent.config import Config
from archive_agent.librarian.audit import log_action
from archive_agent.librarian.budget import budget_report
from archive_agent.librarian.naming import (
    disambiguate,
    disambiguate_folder,
    jellyfin_episode_filename,
    jellyfin_movie_filename,
    jellyfin_movie_folder,
    jellyfin_season_folder,
    jellyfin_show_folder,
)
from archive_agent.librarian.zones import AGENT_MANAGED, Zone, zone_path
from archive_agent.logging import get_logger
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates

__all__ = [
    "BudgetExceededError",
    "PlaceResult",
    "PlacementError",
    "place",
    "promote_movie",
    "promote_show",
]

log = get_logger("archive_agent.librarian.placement")


class PlacementError(Exception):
    """Raised for any structural problem (missing source, wrong zone,
    dest collision beyond disambiguation, etc.)."""


class BudgetExceededError(PlacementError):
    """Raised when placing the file would push agent-managed usage
    above ``max_disk_gb``."""


class PlaceResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    archive_id: str
    zone: Zone
    source_path: Path
    dest_path: Path
    moved: bool
    size_bytes: int


def _status_for_zone(zone: Zone) -> CandidateStatus:
    if zone == Zone.MOVIES or zone == Zone.TV:
        return CandidateStatus.COMMITTED
    if zone == Zone.TV_SAMPLER:
        return CandidateStatus.SAMPLING
    if zone == Zone.RECOMMENDATIONS:
        return CandidateStatus.DOWNLOADED
    raise PlacementError(f"no status mapping for zone {zone!r}")


def _movie_dest(
    zone: Zone,
    config: Config,
    candidate: Candidate,
    source_ext: str,
) -> Path:
    root = zone_path(zone, config)
    folder = jellyfin_movie_folder(candidate.title, candidate.year)
    filename = jellyfin_movie_filename(candidate.title, candidate.year, source_ext)
    return root / folder / filename


def _episode_dest(
    zone: Zone,
    config: Config,
    candidate: Candidate,
    source_ext: str,
    show_title: str | None,
) -> Path:
    root = zone_path(zone, config)
    # Resolve show name: explicit arg wins, else show_id (TMDb id as
    # string), else the candidate's title as a last resort. Phase3 will
    # land a `shows` table with resolved names that this can consult.
    show = show_title or candidate.show_id or candidate.title
    show_folder = jellyfin_show_folder(show)
    season = candidate.season if candidate.season is not None else 1
    episode = candidate.episode if candidate.episode is not None else 0
    season_folder = jellyfin_season_folder(season)
    filename = jellyfin_episode_filename(show, season, episode, candidate.title, source_ext)
    return root / show_folder / season_folder / filename


def place(
    conn: sqlite3.Connection,
    config: Config,
    *,
    candidate: Candidate,
    source_path: Path,
    zone: Zone,
    dry_run: bool = False,
    show_title: str | None = None,
) -> PlaceResult:
    """Move ``source_path`` into ``zone`` with a Jellyfin-compatible name.

    Rejects placement into USER_OWNED zones directly — promotions must
    go through :func:`promote_movie` / :func:`promote_show`. Checks the
    agent budget before moving; raises :class:`BudgetExceededError` if
    the move would exceed ``max_disk_gb``. Disambiguates by appending
    ``(N)`` when the destination file already exists.
    """
    if zone not in AGENT_MANAGED:
        raise PlacementError(
            f"cannot place() directly into USER_OWNED zone {zone!r}; "
            "use promote_movie or promote_show to move into /media/movies or /media/tv"
        )
    if not source_path.exists():
        raise PlacementError(f"source path does not exist: {source_path}")
    size = source_path.stat().st_size

    report = budget_report(config)
    if report.agent_used_bytes + size > report.budget_bytes:
        raise BudgetExceededError(
            f"placing {source_path.name} ({size:,} B) would push agent usage to "
            f"{report.agent_used_bytes + size:,} B above budget "
            f"{report.budget_bytes:,} B ({config.librarian.max_disk_gb} GB)"
        )

    ext = source_path.suffix
    if candidate.content_type == ContentType.MOVIE:
        raw_dest = _movie_dest(zone, config, candidate, ext)
    else:
        raw_dest = _episode_dest(zone, config, candidate, ext, show_title)
    dest = disambiguate(raw_dest)

    if dry_run:
        return PlaceResult(
            archive_id=candidate.archive_id,
            zone=zone,
            source_path=source_path,
            dest_path=dest,
            moved=False,
            size_bytes=size,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(dest))

    new_status = _status_for_zone(zone)
    q_candidates.upsert_candidate(conn, candidate.model_copy(update={"status": new_status}))
    log_action(
        conn,
        action="download",
        zone=zone,
        reason=f"placed {candidate.archive_id} in {zone.value}",
        archive_id=candidate.archive_id,
        show_id=candidate.show_id,
        size_bytes=size,
    )
    log.info(
        "placement_complete",
        archive_id=candidate.archive_id,
        zone=zone.value,
        dest=str(dest),
        size_bytes=size,
    )
    return PlaceResult(
        archive_id=candidate.archive_id,
        zone=zone,
        source_path=source_path,
        dest_path=dest,
        moved=True,
        size_bytes=size,
    )


def _promote(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
    *,
    src_zone: Zone,
    dst_zone: Zone,
    dry_run: bool,
    show_title: str | None = None,
) -> PlaceResult:
    """Shared move logic for movie + show promotions.

    Moves the whole folder (preserves metadata sidecars, subtitles,
    etc.) rather than the video file alone. User-owned zone bypasses
    the budget check — those are outside the accounting entirely.
    """
    src_root = zone_path(src_zone, config)
    dst_root = zone_path(dst_zone, config)

    if candidate.content_type == ContentType.MOVIE:
        folder_name = jellyfin_movie_folder(candidate.title, candidate.year)
    else:
        show = show_title or candidate.show_id or candidate.title
        folder_name = jellyfin_show_folder(show)

    src_folder = src_root / folder_name
    if not src_folder.exists():
        raise PlacementError(
            f"cannot promote {candidate.archive_id}: source folder missing at {src_folder}"
        )

    size = sum(p.stat().st_size for p in src_folder.rglob("*") if p.is_file())
    dst_folder = disambiguate_folder(dst_root / folder_name)

    if dry_run:
        return PlaceResult(
            archive_id=candidate.archive_id,
            zone=dst_zone,
            source_path=src_folder,
            dest_path=dst_folder,
            moved=False,
            size_bytes=size,
        )

    dst_folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_folder), str(dst_folder))

    q_candidates.upsert_candidate(
        conn, candidate.model_copy(update={"status": CandidateStatus.COMMITTED})
    )
    log_action(
        conn,
        action="promote",
        zone=dst_zone,
        reason=f"promoted {candidate.archive_id} {src_zone.value} -> {dst_zone.value}",
        archive_id=candidate.archive_id,
        show_id=candidate.show_id,
        size_bytes=size,
    )
    log.info(
        "promotion_complete",
        archive_id=candidate.archive_id,
        src=src_zone.value,
        dst=dst_zone.value,
        size_bytes=size,
    )
    return PlaceResult(
        archive_id=candidate.archive_id,
        zone=dst_zone,
        source_path=src_folder,
        dest_path=dst_folder,
        moved=True,
        size_bytes=size,
    )


def promote_movie(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
    *,
    dry_run: bool = False,
) -> PlaceResult:
    """Move a movie folder from ``/media/recommendations`` to
    ``/media/movies``. Sets ``status=COMMITTED``. ``/media/movies`` is
    user-owned so there is no budget check."""
    return _promote(
        conn,
        config,
        candidate,
        src_zone=Zone.RECOMMENDATIONS,
        dst_zone=Zone.MOVIES,
        dry_run=dry_run,
    )


def promote_show(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
    *,
    dry_run: bool = False,
    show_title: str | None = None,
) -> PlaceResult:
    """Move a show folder from ``/media/tv-sampler`` to ``/media/tv``.
    The full sampler tree migrates in one ``shutil.move`` so partial
    episodes + metadata stay together."""
    return _promote(
        conn,
        config,
        candidate,
        src_zone=Zone.TV_SAMPLER,
        dst_zone=Zone.TV,
        dry_run=dry_run,
        show_title=show_title,
    )
