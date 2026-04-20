"""Incremental ``TasteProfile`` updates — phase3-05.

The aggregator (phase3-01) keeps dripping new ``taste_events`` into the
DB. This module decides when to evolve the profile forward:

- Not before the previous update's interval has elapsed (hard rate limit).
- Not if there's nothing new to incorporate.
- Not if the delta is too small to justify an LLM call (unless forced).

Each run produces a new append-only row in ``taste_profile_versions``.
History is preserved — see phase3-04 notes.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from archive_agent.config import TasteConfig
from archive_agent.logging import get_logger
from archive_agent.ranking.provider import LLMProvider
from archive_agent.state.models import (
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.state.queries import taste_events as q_taste_events
from archive_agent.state.queries import taste_profile_versions as q_profiles
from archive_agent.taste.profile_ops import preserve_ids
from archive_agent.taste.ratings import RATING_KINDS

_log = get_logger("archive_agent.taste.update")


class UpdatePlan(BaseModel):
    """Snapshot of the update decision. Safe to log / print."""

    current_version: int
    events_since_last: int
    events_to_send: list[TasteEvent] = Field(default_factory=list)
    truncated: int = 0
    should_run: bool
    skip_reason: str | None = None


def _dedupe_ratings(events: list[TasteEvent]) -> list[TasteEvent]:
    """Keep only the newest rating per ``show_id``.

    ADR-013: ratings are latest-wins. If the LLM prompt sees three
    rows for the same show (thumbs flipped twice this week), only
    the current thumb reflects actual intent.
    """
    latest_by_show: dict[str, TasteEvent] = {}
    other: list[TasteEvent] = []
    for event in events:
        if event.kind in RATING_KINDS and event.show_id is not None:
            existing = latest_by_show.get(event.show_id)
            if existing is None or event.timestamp >= existing.timestamp:
                latest_by_show[event.show_id] = event
        else:
            other.append(event)
    return [*other, *latest_by_show.values()]


def plan_update(
    conn: sqlite3.Connection,
    config: TasteConfig,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> UpdatePlan:
    """Decide whether to run the update cycle."""
    current_now = now or datetime.now(UTC)
    current = q_profiles.get_latest_profile(conn)
    if current is None:
        return UpdatePlan(
            current_version=0,
            events_since_last=0,
            should_run=False,
            skip_reason="no profile yet — run bootstrap first",
        )

    raw_events = q_taste_events.list_since(conn, current.updated_at)
    # Exclude events exactly at current.updated_at — we count them as
    # already consumed by the prior profile.
    fresh_events = [e for e in raw_events if e.timestamp > current.updated_at]

    if not fresh_events:
        return UpdatePlan(
            current_version=current.version,
            events_since_last=0,
            should_run=False,
            skip_reason="no events since last update",
        )

    if not force:
        elapsed = current_now - current.updated_at
        if elapsed < timedelta(hours=config.update_interval_hours):
            return UpdatePlan(
                current_version=current.version,
                events_since_last=len(fresh_events),
                should_run=False,
                skip_reason=(
                    f"rate-limited: only {elapsed} since last update "
                    f"(interval: {config.update_interval_hours}h)"
                ),
            )
        if len(fresh_events) < config.min_events_since_last_update:
            return UpdatePlan(
                current_version=current.version,
                events_since_last=len(fresh_events),
                should_run=False,
                skip_reason=(
                    f"below threshold: {len(fresh_events)} events "
                    f"(min {config.min_events_since_last_update})"
                ),
            )

    deduped = _dedupe_ratings(fresh_events)
    # Newest first, then cap.
    deduped.sort(key=lambda e: e.timestamp, reverse=True)
    truncated = max(0, len(deduped) - config.max_events_per_update)
    events_to_send = deduped[: config.max_events_per_update]
    if truncated:
        _log.warning(
            "profile_update_cap_applied",
            truncated=truncated,
            cap=config.max_events_per_update,
        )

    return UpdatePlan(
        current_version=current.version,
        events_since_last=len(fresh_events),
        events_to_send=events_to_send,
        truncated=truncated,
        should_run=True,
    )


async def apply_update(
    conn: sqlite3.Connection,
    provider: LLMProvider,
    plan: UpdatePlan,
) -> TasteProfile:
    """Run the LLM + preserve IDs + insert the new version.

    Caller must ensure ``plan.should_run is True`` — an invalid call
    raises ``ValueError`` rather than silently inserting garbage.
    """
    if not plan.should_run:
        raise ValueError(f"plan.should_run is False: {plan.skip_reason}")

    current = q_profiles.get_latest_profile(conn)
    if current is None:
        raise ValueError("cannot apply_update: no profile exists")

    generated = await provider.update_profile(current, plan.events_to_send)
    final = preserve_ids(current, generated, plan.events_to_send)
    q_profiles.insert_profile(conn, final)
    stored = q_profiles.get_latest_profile(conn)
    assert stored is not None  # we just inserted
    return stored


async def run_if_due(
    conn: sqlite3.Connection,
    provider: LLMProvider,
    config: TasteConfig,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> TasteProfile | None:
    """Convenience: plan + (maybe) apply. Returns None when skipped."""
    plan = plan_update(conn, config, now=now, force=force)
    if not plan.should_run:
        _log.info(
            "profile_update_skipped",
            current_version=plan.current_version,
            reason=plan.skip_reason,
        )
        return None
    result = await apply_update(conn, provider, plan)
    _log.info(
        "profile_update_applied",
        old_version=plan.current_version,
        new_version=result.version,
        events_used=len(plan.events_to_send),
        truncated=plan.truncated,
    )
    return result


# Ignore events we shouldn't use as profile signal — currently none,
# but the constant gives us an obvious hook to exclude noisy kinds
# later (e.g., DEFERRED) without touching plan_update logic.
_IGNORED_PROFILE_KINDS: set[TasteEventKind] = set()


__all__ = [
    "UpdatePlan",
    "apply_update",
    "plan_update",
    "run_if_due",
]
