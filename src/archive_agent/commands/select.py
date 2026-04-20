"""Orchestrate the "user picked this" flow — phase4-04.

``select_candidate`` turns a ``POST /recommendations/{id}/select`` into
a download + place + Jellyfin scan, returning the resolved
``jellyfin_item_id`` the Roku needs to ECP-deep-link (ADR-006).

``commit_show`` bypasses the sampler-first policy (ADR-004) and
enqueues every episode candidate for a show for download. Used by the
Roku's long-press "give me the whole thing" flow.

Both functions keep the request path short by:

- Returning ``ready`` fast when ``jellyfin_item_id`` is already
  populated (idempotent) — the daemon's background passes eventually
  land on the same state, so a second ``/select`` doesn't duplicate
  work.
- For shows, handing off to ``tv_sampler.step_show`` rather than
  downloading every episode synchronously.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from archive_agent.api.serializers import EpisodeInfo
from archive_agent.archive.downloader import (
    DownloadRequest,
    DownloadResult,
    download_one,
)
from archive_agent.config import Config
from archive_agent.jellyfin.client import JellyfinClient
from archive_agent.jellyfin.placement import scan_and_resolve
from archive_agent.librarian.placement import place
from archive_agent.librarian.tv_sampler import SamplerResult, step_show
from archive_agent.librarian.zones import Zone
from archive_agent.logging import get_logger
from archive_agent.state.models import (
    Candidate,
    CandidateStatus,
    ContentType,
)
from archive_agent.state.queries import candidates as q_candidates

_log = get_logger("archive_agent.commands.select")

SelectStatus = Literal["ready", "queued", "failed"]


class CandidateNotFoundError(RuntimeError):
    """Raised when ``select_candidate`` is called with an unknown archive_id."""


# Keep references to fire-and-forget commit tasks so the event loop
# doesn't GC them mid-download.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


class SelectResult(BaseModel):
    archive_id: str
    status: SelectStatus
    jellyfin_item_id: str | None = None
    play_start_ticks: int = 0
    next_episode: EpisodeInfo | None = None
    detail: str = ""


class CommitResult(BaseModel):
    show_id: str
    enqueued_downloads: int
    estimated_gb: float


def _staging_dir(config: Config) -> Path:
    return Path("/tmp/archive-agent/staging") / config.paths.state_db.stem


async def _resolve_after_placement(
    config: Config, archive_id: str, zone: Zone, conn: sqlite3.Connection
) -> str | None:
    """Trigger the Jellyfin scan + poll for the new item ID.

    Split out so tests can monkey-patch ``scan_and_resolve`` without
    stubbing the whole JellyfinClient construction chain.
    """
    async with JellyfinClient(
        config.jellyfin.url,
        config.jellyfin.api_key,
        config.jellyfin.user_id,
    ) as client:
        return await scan_and_resolve(client, conn, archive_id=archive_id, zone=zone)


async def _download_and_place(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
    *,
    zone: Zone,
) -> tuple[DownloadResult, Path | None]:
    """Download the candidate into staging, move it into ``zone``.

    Returns the raw DownloadResult plus the final placed path (or None
    if either step failed).
    """
    req = DownloadRequest(
        archive_id=candidate.archive_id,
        zone=zone,
        dest_dir=_staging_dir(config),
    )
    dl = await download_one(
        req, conn, max_concurrent=config.librarian.max_concurrent_downloads
    )
    if dl.status not in {"done", "skipped"} or dl.file_path is None:
        return dl, None
    placement = place(
        conn,
        config,
        candidate=candidate,
        source_path=dl.file_path,
        zone=zone,
    )
    return dl, placement.dest_path


async def select_candidate(
    conn: sqlite3.Connection,
    config: Config,
    archive_id: str,
    *,
    play: bool = True,
) -> SelectResult:
    """Drive the full select flow for one archive_id."""
    candidate = q_candidates.get_by_archive_id(conn, archive_id)
    if candidate is None:
        raise CandidateNotFoundError(archive_id)

    if candidate.jellyfin_item_id is not None:
        next_ep = _next_episode_for(candidate)
        _log.info(
            "select_idempotent_ready",
            archive_id=archive_id,
            jellyfin_item_id=candidate.jellyfin_item_id,
        )
        return SelectResult(
            archive_id=archive_id,
            status="ready",
            jellyfin_item_id=candidate.jellyfin_item_id,
            next_episode=next_ep,
        )

    if candidate.content_type == ContentType.MOVIE:
        return await _select_movie(conn, config, candidate, play=play)
    return await _select_show(conn, config, candidate)


async def _select_movie(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
    *,
    play: bool,
) -> SelectResult:
    dl, _placed = await _download_and_place(
        conn, config, candidate, zone=Zone.RECOMMENDATIONS
    )
    if dl.status == "failed":
        _log.warning("select_download_failed", archive_id=candidate.archive_id, error=dl.error)
        return SelectResult(
            archive_id=candidate.archive_id,
            status="failed",
            detail=f"download failed: {dl.error or 'unknown'}",
        )

    item_id = await _resolve_after_placement(
        config, candidate.archive_id, Zone.RECOMMENDATIONS, conn
    )
    if item_id is None:
        return SelectResult(
            archive_id=candidate.archive_id,
            status="queued",
            detail="downloaded; awaiting Jellyfin scan",
        )
    # Mark the candidate downloaded/committed-ish — the candidate's
    # status transitions are owned by the librarian in other phases;
    # at minimum update its ``jellyfin_item_id`` via the status move
    # so /recommendations returns the right value next time.
    q_candidates.update_status(
        conn, candidate.archive_id, CandidateStatus.DOWNLOADED
    )
    _log.info(
        "select_movie_ready",
        archive_id=candidate.archive_id,
        jellyfin_item_id=item_id,
        play=play,
    )
    return SelectResult(
        archive_id=candidate.archive_id,
        status="ready",
        jellyfin_item_id=item_id,
    )


async def _select_show(
    conn: sqlite3.Connection,
    config: Config,
    candidate: Candidate,
) -> SelectResult:
    show_id = candidate.show_id or candidate.archive_id
    sampler: SamplerResult = await step_show(
        conn, config, show_id, download_one
    )

    # After the step, check whether any episode for this show has a
    # Jellyfin item id we can deep-link into. First finished-or-
    # unfinished episode wins.
    eps = q_candidates.list_by_show(conn, show_id)
    for ep in eps:
        if ep.jellyfin_item_id is not None:
            next_ep = EpisodeInfo(
                season=ep.season or 1,
                episode=ep.episode or 1,
                title=ep.title,
            )
            return SelectResult(
                archive_id=candidate.archive_id,
                status="ready",
                jellyfin_item_id=ep.jellyfin_item_id,
                next_episode=next_ep,
            )

    return SelectResult(
        archive_id=candidate.archive_id,
        status="queued",
        detail=f"sampler_{sampler.action}",
    )


def _next_episode_for(candidate: Candidate) -> EpisodeInfo | None:
    if candidate.content_type == ContentType.MOVIE:
        return None
    if candidate.season is None or candidate.episode is None:
        return None
    return EpisodeInfo(
        season=candidate.season,
        episode=candidate.episode,
        title=candidate.title,
    )


async def commit_show(
    conn: sqlite3.Connection,
    config: Config,
    show_id: str,
) -> CommitResult:
    """Bypass the sampler and queue every episode directly into /media/tv.

    Downloads are kicked off in a background task so the request
    returns quickly. The audit trail lives in ``downloads`` +
    ``librarian_actions`` (via the downloader + librarian itself).
    """
    episodes = q_candidates.list_by_show(conn, show_id)
    episodes = [c for c in episodes if c.content_type == ContentType.EPISODE]
    if not episodes:
        return CommitResult(
            show_id=show_id, enqueued_downloads=0, estimated_gb=0.0
        )

    size_bytes = sum(c.size_bytes or 0 for c in episodes)

    async def _enqueue() -> None:
        for ep in episodes:
            try:
                await _download_and_place(conn, config, ep, zone=Zone.TV)
            except Exception as exc:
                _log.error(
                    "commit_episode_enqueue_failed",
                    show_id=show_id,
                    archive_id=ep.archive_id,
                    error=type(exc).__name__,
                    detail=str(exc),
                )

    # Fire-and-forget. Holding a reference in the module-level set
    # keeps the event loop from GC'ing the task mid-download; the
    # done callback drops it when the coroutine returns.
    task = asyncio.create_task(_enqueue())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    _log.info(
        "commit_show_started",
        show_id=show_id,
        episodes=len(episodes),
        when=datetime.now(UTC).isoformat(),
    )
    return CommitResult(
        show_id=show_id,
        enqueued_downloads=len(episodes),
        estimated_gb=round(size_bytes / 1e9, 2),
    )


__all__ = [
    "CandidateNotFoundError",
    "CommitResult",
    "SelectResult",
    "SelectStatus",
    "commit_show",
    "select_candidate",
]
