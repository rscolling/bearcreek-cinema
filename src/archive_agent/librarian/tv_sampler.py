"""Sampler-first TV flow (phase2-08).

The sampler-first pattern is the reason TV doesn't blow the disk
budget: we only commit a show's full storage cost once the household
has actually watched enough to signal they like it.

Phases for any given ``show_id``:

1. **Start sampling** — no ``show_state`` yet. Pick the first
   ``sampler_episode_count`` episodes of Season 1, queue downloads,
   place into ``/media/tv-sampler``. Record ``started_at``.
2. **Wait** — sampler downloads in flight, or downloaded but the
   household hasn't finished enough episodes yet (and we're still
   within ``promote_window_days``).
3. **Promote** — sampler episodes complete, ``episodes_finished >=
   promote_after_n_finished``, and the gap between ``started_at`` and
   ``last_playback_at`` is within the window. Queue the remainder of
   Season 1 straight into ``/media/tv`` and move the sampler folder
   over. Post-promotion, additional seasons get queued lazily as the
   household watches at least one episode of the current season.
4. **Evict** — sampler window expired without enough engagement. This
   function returns an ``evict`` decision; the actual TTL-driven
   sweep lives in phase2-07 — we don't double-delete.

``decide_for_show`` is pure: it reads ``show_state`` + ``candidates``
and returns a ``SamplerDecision``. ``step_show`` executes the
decision (download + place + state update).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from archive_agent.config import Config, LibrarianTvConfig
from archive_agent.librarian.placement import PlaceResult, place, promote_show
from archive_agent.librarian.zones import Zone
from archive_agent.logging import get_logger
from archive_agent.state.models import Candidate, CandidateStatus, ShowState
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import show_state as q_show_state

if TYPE_CHECKING:
    # ``archive.downloader`` pulls in ``librarian.zones`` which pulls us
    # back in via ``librarian/__init__.py`` — keep these references in
    # the type-check-only namespace to avoid a circular import at runtime.
    from archive_agent.archive.downloader import DownloadRequest, DownloadResult

__all__ = [
    "Downloader",
    "SamplerAction",
    "SamplerDecision",
    "SamplerResult",
    "decide_for_show",
    "should_promote",
    "step_all_shows",
    "step_show",
]

log = get_logger("archive_agent.librarian.tv_sampler")

SamplerAction = Literal["start_sampling", "promote", "wait", "evict"]


class Downloader(Protocol):
    """A pluggable download function with the shape of
    ``archive.downloader.download_one``. Used so tests can inject a
    fake downloader without going through the real ``internetarchive``
    library."""

    async def __call__(self, req: DownloadRequest, conn: sqlite3.Connection) -> DownloadResult: ...


class SamplerDecision(BaseModel):
    show_id: str
    action: SamplerAction
    reason: str
    episodes_to_download: list[Candidate] = []


class SamplerResult(BaseModel):
    show_id: str
    action: SamplerAction
    reason: str
    episodes_attempted: int = 0
    episodes_placed: int = 0
    promoted: bool = False
    errors: list[str] = []


# --- pure decision logic ------------------------------------------------


def should_promote(
    state: ShowState,
    tv_cfg: LibrarianTvConfig,
    now: datetime,
) -> bool:
    """True when the household has watched enough sampler episodes,
    within the window, to warrant full-season download. The window is
    measured from ``started_at`` (first sampler episode downloaded) to
    ``last_playback_at`` (most recent real watch) — not against ``now``,
    so a household that watched a year ago and came back this week
    still promotes."""
    if state.episodes_finished < tv_cfg.promote_after_n_finished:
        return False
    if state.last_playback_at is None:
        return False
    window = timedelta(days=tv_cfg.promote_window_days)
    return (state.last_playback_at - state.started_at) <= window


def _season_episode_key(c: Candidate) -> tuple[int, int]:
    return (c.season or 0, c.episode or 0)


def _sampler_targets(episodes: list[Candidate], count: int) -> list[Candidate]:
    """First ``count`` Season-1 episodes eligible for sampling.

    "Eligible" = status is NEW/RANKED/APPROVED (ie not already
    downloading or placed). If episode 1 is missing we slide forward;
    ARCHITECTURE.md calls this out as expected on Archive.org.
    """
    s1 = [
        c
        for c in episodes
        if c.season == 1
        and c.status in (CandidateStatus.NEW, CandidateStatus.RANKED, CandidateStatus.APPROVED)
    ]
    s1.sort(key=_season_episode_key)
    return s1[:count]


def _current_max_committed_season(episodes: list[Candidate]) -> int | None:
    seasons = [
        c.season for c in episodes if c.status == CandidateStatus.COMMITTED and c.season is not None
    ]
    return max(seasons) if seasons else None


def _next_season_targets(episodes: list[Candidate], next_season: int) -> list[Candidate]:
    targets = [
        c
        for c in episodes
        if c.season == next_season
        and c.status in (CandidateStatus.NEW, CandidateStatus.RANKED, CandidateStatus.APPROVED)
    ]
    targets.sort(key=_season_episode_key)
    return targets


def decide_for_show(
    conn: sqlite3.Connection,
    config: Config,
    show_id: str,
    *,
    now: datetime | None = None,
) -> SamplerDecision:
    """What should the TV sampler do next for this show? Pure function —
    reads state, returns the decision, does not mutate."""
    effective_now = now or datetime.now(UTC)
    tv_cfg = config.librarian.tv
    state = q_show_state.get(conn, show_id)
    episodes = [
        c
        for c in q_candidates.list_by_show(conn, show_id)
        if c.season is not None and c.episode is not None
    ]

    if not episodes:
        return SamplerDecision(
            show_id=show_id, action="wait", reason="no episode candidates for this show"
        )

    sampler_eps = [c for c in episodes if c.status == CandidateStatus.SAMPLING]
    committed_eps = [c for c in episodes if c.status == CandidateStatus.COMMITTED]
    sampler_count = len(sampler_eps)

    # --- Already committed: season advancement path ----------------------
    if committed_eps:
        current_season = _current_max_committed_season(episodes)
        assert current_season is not None
        next_targets = _next_season_targets(episodes, current_season + 1)
        if not next_targets:
            return SamplerDecision(
                show_id=show_id,
                action="wait",
                reason=f"committed through S{current_season:02d}; no further seasons available",
            )
        if state is not None and state.episodes_finished >= 1:
            return SamplerDecision(
                show_id=show_id,
                action="promote",
                reason=f"advancing to S{current_season + 1:02d} ({len(next_targets)} episodes)",
                episodes_to_download=next_targets,
            )
        return SamplerDecision(
            show_id=show_id,
            action="wait",
            reason=f"committed through S{current_season:02d}; awaiting first watch before advancing",
        )

    # --- No state yet: bootstrap the sampler ----------------------------
    if state is None and sampler_count == 0:
        targets = _sampler_targets(episodes, tv_cfg.sampler_episode_count)
        if not targets:
            return SamplerDecision(
                show_id=show_id,
                action="wait",
                reason="no Season 1 candidates available to sample",
            )
        return SamplerDecision(
            show_id=show_id,
            action="start_sampling",
            reason=f"starting sampler with {len(targets)} episode(s)",
            episodes_to_download=targets,
        )

    # --- Sampling in progress -------------------------------------------
    assert state is not None
    if sampler_count < tv_cfg.sampler_episode_count:
        return SamplerDecision(
            show_id=show_id,
            action="wait",
            reason=(
                f"sampler partial ({sampler_count}/{tv_cfg.sampler_episode_count} episodes placed)"
            ),
        )

    # Full sampler. Decide based on engagement.
    if should_promote(state, tv_cfg, effective_now):
        # Queue remaining S1 episodes that aren't already in sampler / committed
        handled_eps = {(c.season, c.episode) for c in sampler_eps + committed_eps}
        remaining = [
            c
            for c in episodes
            if c.season == 1
            and (c.season, c.episode) not in handled_eps
            and c.status in (CandidateStatus.NEW, CandidateStatus.RANKED, CandidateStatus.APPROVED)
        ]
        remaining.sort(key=_season_episode_key)
        return SamplerDecision(
            show_id=show_id,
            action="promote",
            reason=(
                f"sampler engagement threshold met "
                f"({state.episodes_finished}/{tv_cfg.promote_after_n_finished} finished)"
            ),
            episodes_to_download=remaining,
        )

    window = timedelta(days=tv_cfg.promote_window_days)
    elapsed_days = (effective_now - state.started_at).days
    if (effective_now - state.started_at) > window:
        return SamplerDecision(
            show_id=show_id,
            action="evict",
            reason=(
                f"sampler window expired after {elapsed_days}d "
                f"(only {state.episodes_finished} finished; phase2-07 handles the actual sweep)"
            ),
        )

    return SamplerDecision(
        show_id=show_id,
        action="wait",
        reason=(
            f"sampling: {state.episodes_finished}/{tv_cfg.promote_after_n_finished} finished, "
            f"{elapsed_days}d of {tv_cfg.promote_window_days}d elapsed"
        ),
    )


# --- execution ----------------------------------------------------------


async def _download_and_place(
    conn: sqlite3.Connection,
    config: Config,
    episodes: list[Candidate],
    zone: Zone,
    downloader: Downloader,
    *,
    show_title: str | None,
    staging_root: Path,
) -> tuple[int, list[str]]:
    """Download each episode then place it into ``zone``. Returns
    (placed_count, errors)."""
    # Runtime import: avoids the circular import with
    # ``archive.downloader`` (which imports ``librarian.zones``).
    from archive_agent.archive.downloader import DownloadRequest as _DLReq

    placed = 0
    errors: list[str] = []
    for ep in episodes:
        req = _DLReq(
            archive_id=ep.archive_id,
            zone=zone,
            dest_dir=staging_root,
        )
        try:
            dl = await downloader(req, conn)
        except Exception as exc:  # downloader contract raises on hard errors
            errors.append(f"{ep.archive_id}: download failed: {exc}")
            continue
        if dl.status not in ("done", "skipped") or dl.file_path is None:
            errors.append(
                f"{ep.archive_id}: download ended status={dl.status} (error={dl.error!r})"
            )
            continue
        try:
            res: PlaceResult = place(
                conn,
                config,
                candidate=ep,
                source_path=dl.file_path,
                zone=zone,
                show_title=show_title,
            )
        except Exception as exc:
            errors.append(f"{ep.archive_id}: place failed: {exc}")
            continue
        if res.moved:
            placed += 1
    return placed, errors


def _staging_root(config: Config) -> Path:
    """Where completed downloads land before placement. Shared across
    phase2-04/06/08 — keep in /tmp so it's always writable in the
    container."""
    return Path("/tmp/archive-agent/staging")


async def step_show(
    conn: sqlite3.Connection,
    config: Config,
    show_id: str,
    downloader: Downloader,
    *,
    show_title: str | None = None,
    now: datetime | None = None,
) -> SamplerResult:
    """Execute one pass of the TV sampler state machine for this show."""
    effective_now = now or datetime.now(UTC)
    decision = decide_for_show(conn, config, show_id, now=effective_now)
    result = SamplerResult(
        show_id=show_id,
        action=decision.action,
        reason=decision.reason,
        episodes_attempted=len(decision.episodes_to_download),
    )
    log.info(
        "sampler_decision",
        show_id=show_id,
        action=decision.action,
        reason=decision.reason,
        episodes_queued=len(decision.episodes_to_download),
    )

    if decision.action == "start_sampling":
        # Record the sampler start timestamp so the window clock runs
        state = q_show_state.get(conn, show_id) or ShowState(
            show_id=show_id,
            episodes_finished=0,
            episodes_abandoned=0,
            episodes_available=len(decision.episodes_to_download),
            started_at=effective_now,
        )
        if q_show_state.get(conn, show_id) is None:
            state = state.model_copy(update={"started_at": effective_now})
        q_show_state.upsert(conn, state)

        placed, errs = await _download_and_place(
            conn,
            config,
            decision.episodes_to_download,
            Zone.TV_SAMPLER,
            downloader,
            show_title=show_title,
            staging_root=_staging_root(config),
        )
        result.episodes_placed = placed
        result.errors = errs
        return result

    if decision.action == "promote":
        placed, errs = await _download_and_place(
            conn,
            config,
            decision.episodes_to_download,
            Zone.TV,
            downloader,
            show_title=show_title,
            staging_root=_staging_root(config),
        )
        result.episodes_placed = placed
        result.errors = errs

        # Move the sampler tree to /media/tv (if one exists — season-N
        # advancement doesn't have a sampler folder to move).
        sampler_representative = next(
            (
                c
                for c in q_candidates.list_by_show(conn, show_id)
                if c.status == CandidateStatus.SAMPLING
            ),
            None,
        )
        if sampler_representative is not None:
            try:
                promote_show(
                    conn,
                    config,
                    sampler_representative,
                    show_title=show_title,
                )
                result.promoted = True
            except Exception as exc:  # promote_show raises PlacementError
                errs.append(f"promote_show failed: {exc}")
        return result

    # wait / evict — no disk mutation this pass.
    return result


async def step_all_shows(
    conn: sqlite3.Connection,
    config: Config,
    downloader: Downloader,
    *,
    now: datetime | None = None,
) -> list[SamplerResult]:
    """Iterate every ``show_id`` with at least one episode candidate
    and run :func:`step_show` for each. Serial on purpose — the
    downloader's own semaphore handles parallelism between episodes
    within a show; spraying across shows would multiply network load
    on Archive.org."""
    rows = conn.execute(
        "SELECT DISTINCT show_id FROM candidates "
        "WHERE content_type = 'episode' AND show_id IS NOT NULL"
    ).fetchall()
    results: list[SamplerResult] = []
    for row in rows:
        results.append(await step_show(conn, config, row["show_id"], downloader, now=now))
    return results
