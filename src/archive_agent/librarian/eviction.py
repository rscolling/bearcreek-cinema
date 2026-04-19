"""Eviction planning and execution for agent-managed zones.

Policy (from ARCHITECTURE.md):

1. ``/media/recommendations`` untouched ``recommendations_ttl_days``+ →
   delete oldest first
2. ``/media/tv-sampler`` untouched ``tv_sampler_ttl_days``+ → delete
3. ``/media/movies`` — **never** auto-evicted (user-owned)
4. Committed ``/media/tv`` — requires propose + grace period; this
   phase only leaves the stub (``propose_committed_tv_eviction``)
5. Still over budget after 1-3 → emit a loud WARN; don't touch
   committed content silently

Touched timestamps prefer ``show_state.last_playback_at`` (for shows)
when present, then fall back to filesystem mtime and candidate
``discovered_at``. Never ``atime`` — ext4 with ``noatime`` (the
default) makes that unreliable.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from archive_agent.config import Config
from archive_agent.librarian.audit import log_action
from archive_agent.librarian.budget import budget_report
from archive_agent.librarian.zones import Zone, zone_path
from archive_agent.logging import get_logger
from archive_agent.state.models import Candidate, CandidateStatus
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import show_state as q_show_state

__all__ = [
    "EvictionItem",
    "EvictionPlan",
    "EvictionReason",
    "EvictionResult",
    "execute_eviction",
    "last_touched_at",
    "plan_eviction",
    "propose_committed_tv_eviction",
]

log = get_logger("archive_agent.librarian.eviction")

EvictionReason = Literal[
    "recommendation_untouched",
    "sampler_untouched",
    "sampler_failed_promotion",
]


class EvictionItem(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    zone: Zone
    archive_id: str | None = None
    show_id: str | None = None
    size_bytes: int
    reason: EvictionReason
    last_touched_at: datetime


class EvictionPlan(BaseModel):
    would_free_bytes: int = 0
    items: list[EvictionItem] = []
    still_over_budget: bool = False
    blocked_reason: str | None = None


class EvictionResult(BaseModel):
    planned: int = 0
    evicted: int = 0
    failed: int = 0
    freed_bytes: int = 0
    still_over_budget: bool = False


# --- touched-time resolution -------------------------------------------


def _folder_latest_mtime(folder: Path) -> float:
    """Largest ``st_mtime`` among the folder and its descendants."""
    latest = folder.stat().st_mtime
    for p in folder.rglob("*"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > latest:
            latest = mtime
    return latest


def last_touched_at(
    conn: sqlite3.Connection,
    candidate: Candidate | None,
    folder: Path | None,
) -> datetime:
    """Compute the last-touched timestamp for an agent-managed folder.

    Sources (the max wins):
    - ``show_state.last_playback_at`` — where applicable
    - ``candidate.discovered_at`` — a floor
    - filesystem mtime of the folder tree
    """
    stamps: list[datetime] = []
    if candidate is not None:
        stamps.append(candidate.discovered_at)
        if candidate.show_id:
            state = q_show_state.get(conn, candidate.show_id)
            if state is not None and state.last_playback_at is not None:
                stamps.append(state.last_playback_at)
    if folder is not None and folder.exists():
        stamps.append(datetime.fromtimestamp(_folder_latest_mtime(folder), UTC))
    if not stamps:
        # No signals at all — treat as fresh so we never accidentally
        # evict something we don't recognize.
        return datetime.now(UTC)
    return max(stamps)


def _find_candidate_for_folder(conn: sqlite3.Connection, folder: Path) -> Candidate | None:
    """Match a folder back to a candidate via the ``downloads.path`` row.
    Returns None if the candidate can't be located."""
    row = conn.execute(
        "SELECT archive_id FROM downloads "
        "WHERE status = 'done' AND path LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (f"%{folder.name}%",),
    ).fetchone()
    if row is None:
        return None
    return q_candidates.get_by_archive_id(conn, row["archive_id"])


# --- planning ----------------------------------------------------------


def _collect_zone_items(
    conn: sqlite3.Connection,
    config: Config,
    zone: Zone,
    ttl: timedelta,
    reason: EvictionReason,
    now: datetime,
) -> list[EvictionItem]:
    root = zone_path(zone, config)
    if not root.exists():
        return []
    items: list[EvictionItem] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        candidate = _find_candidate_for_folder(conn, child)
        touched = last_touched_at(conn, candidate, child)
        if now - touched < ttl:
            continue
        size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
        items.append(
            EvictionItem(
                path=child,
                zone=zone,
                archive_id=candidate.archive_id if candidate else None,
                show_id=candidate.show_id if candidate else None,
                size_bytes=size,
                reason=reason,
                last_touched_at=touched,
            )
        )
    return items


def plan_eviction(
    conn: sqlite3.Connection,
    config: Config,
    *,
    now: datetime | None = None,
) -> EvictionPlan:
    """Walk recommendations + tv-sampler zones, pick oldest-stale items
    until cumulative frees reach the agent's overage. ``now`` is
    injectable for deterministic tests."""
    effective_now = now or datetime.now(UTC)

    report = budget_report(config)
    if not report.over_budget:
        return EvictionPlan()

    overage = report.agent_used_bytes - report.budget_bytes
    candidates: list[EvictionItem] = []
    candidates.extend(
        _collect_zone_items(
            conn,
            config,
            Zone.RECOMMENDATIONS,
            timedelta(days=config.librarian.recommendations_ttl_days),
            "recommendation_untouched",
            effective_now,
        )
    )
    candidates.extend(
        _collect_zone_items(
            conn,
            config,
            Zone.TV_SAMPLER,
            timedelta(days=config.librarian.tv_sampler_ttl_days),
            "sampler_untouched",
            effective_now,
        )
    )
    candidates.sort(key=lambda i: i.last_touched_at)

    selected: list[EvictionItem] = []
    freed = 0
    for item in candidates:
        if freed >= overage:
            break
        selected.append(item)
        freed += item.size_bytes

    still_over = freed < overage
    blocked_reason: str | None = None
    if still_over:
        remaining = overage - freed
        blocked_reason = (
            f"{remaining:,} bytes still over budget after planned evictions; "
            "committed /media/tv is never auto-evicted — see "
            "`archive-agent librarian status`"
        )

    return EvictionPlan(
        would_free_bytes=freed,
        items=selected,
        still_over_budget=still_over,
        blocked_reason=blocked_reason,
    )


# --- execution ---------------------------------------------------------


def _delete(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def execute_eviction(
    plan: EvictionPlan,
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> EvictionResult:
    """Apply the plan. One ``librarian_actions`` row per successful
    deletion. Failures (permission, file-busy) log and skip — one bad
    item doesn't abort the batch. If the plan is ``still_over_budget``,
    emits one loud WARN event regardless of ``dry_run``."""
    result = EvictionResult(
        planned=len(plan.items),
        still_over_budget=plan.still_over_budget,
    )

    if plan.still_over_budget:
        log.warning(
            "eviction_blocked",
            would_free_bytes=plan.would_free_bytes,
            detail=plan.blocked_reason,
        )

    if dry_run:
        return result

    for item in plan.items:
        try:
            _delete(item.path)
        except OSError as exc:
            log.warning(
                "eviction_delete_failed",
                path=str(item.path),
                error=str(exc),
            )
            result.failed += 1
            continue

        result.evicted += 1
        result.freed_bytes += item.size_bytes
        log_action(
            conn,
            action="evict",
            zone=item.zone,
            reason=item.reason,
            archive_id=item.archive_id,
            show_id=item.show_id,
            size_bytes=item.size_bytes,
        )
        if item.archive_id is not None:
            candidate = q_candidates.get_by_archive_id(conn, item.archive_id)
            if candidate is not None:
                q_candidates.upsert_candidate(
                    conn,
                    candidate.model_copy(update={"status": CandidateStatus.EXPIRED}),
                )

    return result


# --- committed-TV stub (deferred) --------------------------------------


def propose_committed_tv_eviction(
    conn: sqlite3.Connection,
    show_id: str,
    *,
    grace_days: int,
) -> int:
    """Deliberate stub — write a ``librarian_actions`` row for a future
    committed-TV eviction proposal. The actual delete logic waits for a
    user-approval flow (phase6 or manual). We never auto-delete
    committed content.

    Returns the audit row id so a caller could track proposals over
    time."""
    return log_action(
        conn,
        action="skip",
        zone=Zone.TV,
        reason=f"committed_eviction_proposed:grace_days={grace_days}",
        show_id=show_id,
    )
