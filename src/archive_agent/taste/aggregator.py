"""Show-state aggregator — ADR-004.

Raw episode playback (``episode_watches``) does not touch the taste
profile directly. Instead, this aggregator rolls episodes up into
per-show state and emits at most one ``BINGE_POSITIVE`` /
``BINGE_NEGATIVE`` ``TasteEvent`` per show per threshold crossing.

Running it twice against the same data produces the same side
effects (idempotent). The guard is ``show_state.last_emitted_event``:
once a show has emitted a positive event, we don't re-emit it unless
the *polarity* changes (positive -> negative or vice versa).

Explicit-rating events (ADR-013, ``source='roku_api'``) are written
elsewhere and deliberately ignored here — see ``taste.ratings``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from archive_agent.config import TasteConfig
from archive_agent.state.models import (
    ContentType,
    ShowState,
    TasteEvent,
    TasteEventKind,
)
from archive_agent.state.queries import (
    show_state as q_show_state,
)
from archive_agent.state.queries import (
    taste_events as q_taste_events,
)

# A watch counts as "finished" if its peak completion reached this.
# Consistent with Jellyfin's default "watched" threshold (90%).
_FINISHED_THRESHOLD = 0.9


BingeAction = Literal["emit_positive", "emit_negative", "skip"]


class BingeOutcome(BaseModel):
    show_id: str
    action: BingeAction
    reason: str
    event: TasteEvent | None = None


def evaluate_show(
    state: ShowState,
    config: TasteConfig,
    now: datetime,
) -> BingeOutcome:
    """Pure decision function — no DB access, no side effects.

    Inputs are the latest ``ShowState`` (already refreshed from
    ``episode_watches``) and the ``TasteConfig`` thresholds. Returns
    the action the aggregator should take next.
    """
    show_id = state.show_id

    if state.episodes_available <= 0:
        return BingeOutcome(show_id=show_id, action="skip", reason="no episodes available")

    # "Season completion" shortcut: watched everything we have, and
    # the show is substantial enough that it means something.
    if (
        state.episodes_finished >= state.episodes_available
        and state.episodes_available >= config.season_complete_min_episodes
    ):
        polarity = TasteEventKind.BINGE_POSITIVE
        if state.last_emitted_event == polarity:
            return BingeOutcome(
                show_id=show_id,
                action="skip",
                reason="already emitted BINGE_POSITIVE",
            )
        return _positive_outcome(state, config, now, reason="season complete")

    pct = state.episodes_finished / state.episodes_available

    # Positive: finished the threshold share within the window.
    if pct >= config.binge_positive_completion_pct:
        if state.last_playback_at is None:
            # Can't score without recent-ish playback evidence.
            return BingeOutcome(show_id=show_id, action="skip", reason="no last_playback_at")
        binge_span_days = (state.last_playback_at - state.started_at).days
        if binge_span_days <= config.binge_positive_window_days:
            if state.last_emitted_event == TasteEventKind.BINGE_POSITIVE:
                return BingeOutcome(
                    show_id=show_id,
                    action="skip",
                    reason="already emitted BINGE_POSITIVE",
                )
            return _positive_outcome(
                state,
                config,
                now,
                reason=(
                    f"finished {pct:.0%} within {binge_span_days}d "
                    f"(threshold {config.binge_positive_completion_pct:.0%} / "
                    f"{config.binge_positive_window_days}d)"
                ),
            )

    # Negative: watched very little and went quiet past the inactivity window.
    if state.episodes_finished <= config.binge_negative_max_episodes:
        if state.last_playback_at is None:
            return BingeOutcome(show_id=show_id, action="skip", reason="no last_playback_at")
        inactivity_days = (now - state.last_playback_at).days
        if inactivity_days >= config.binge_negative_inactivity_days:
            if state.last_emitted_event == TasteEventKind.BINGE_NEGATIVE:
                return BingeOutcome(
                    show_id=show_id,
                    action="skip",
                    reason="already emitted BINGE_NEGATIVE",
                )
            return _negative_outcome(
                state,
                config,
                now,
                reason=(
                    f"{state.episodes_finished} finished, "
                    f"{inactivity_days}d inactive "
                    f"(threshold <={config.binge_negative_max_episodes} / "
                    f">={config.binge_negative_inactivity_days}d)"
                ),
            )

    return BingeOutcome(
        show_id=show_id,
        action="skip",
        reason=(
            f"below thresholds: finished={state.episodes_finished}/"
            f"{state.episodes_available} pct={pct:.0%}"
        ),
    )


def _positive_outcome(
    state: ShowState, config: TasteConfig, now: datetime, *, reason: str
) -> BingeOutcome:
    event = TasteEvent(
        timestamp=now,
        content_type=ContentType.SHOW,
        show_id=state.show_id,
        kind=TasteEventKind.BINGE_POSITIVE,
        strength=config.binge_positive_strength,
        source="playback",
    )
    return BingeOutcome(show_id=state.show_id, action="emit_positive", reason=reason, event=event)


def _negative_outcome(
    state: ShowState, config: TasteConfig, now: datetime, *, reason: str
) -> BingeOutcome:
    event = TasteEvent(
        timestamp=now,
        content_type=ContentType.SHOW,
        show_id=state.show_id,
        kind=TasteEventKind.BINGE_NEGATIVE,
        strength=config.binge_negative_strength,
        source="playback",
    )
    return BingeOutcome(show_id=state.show_id, action="emit_negative", reason=reason, event=event)


def refresh_show_state(conn: sqlite3.Connection, show_id: str) -> ShowState | None:
    """Recompute ``show_state`` for one show from ``episode_watches`` +
    ``candidates``. Persists the result and returns it.

    Returns ``None`` when the show has no watches *and* no episode
    candidates — there's nothing to aggregate yet. Preserves
    ``last_emitted_event`` / ``last_emitted_at`` from the existing row
    so the aggregator's idempotence guard survives a refresh.
    """
    finished_count = 0
    abandoned_count = 0
    last_playback_iso: str | None = None
    earliest_watch_iso: str | None = None

    per_episode = conn.execute(
        """
        SELECT season, episode,
               MAX(completion_pct) AS max_pct,
               MAX(timestamp)      AS max_ts,
               MIN(timestamp)      AS min_ts
          FROM episode_watches
         WHERE show_id = ?
         GROUP BY season, episode
        """,
        (show_id,),
    ).fetchall()

    for row in per_episode:
        if row["max_pct"] >= _FINISHED_THRESHOLD:
            finished_count += 1
        else:
            abandoned_count += 1
        if last_playback_iso is None or row["max_ts"] > last_playback_iso:
            last_playback_iso = row["max_ts"]
        if earliest_watch_iso is None or row["min_ts"] < earliest_watch_iso:
            earliest_watch_iso = row["min_ts"]

    available_row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM candidates
         WHERE show_id = ? AND content_type = 'episode'
        """,
        (show_id,),
    ).fetchone()
    episodes_available = int(available_row["c"]) if available_row else 0

    if not per_episode and episodes_available == 0:
        return None

    existing = q_show_state.get(conn, show_id)
    started_at: datetime
    if existing is not None:
        started_at = existing.started_at
    elif earliest_watch_iso is not None:
        started_at = datetime.fromisoformat(earliest_watch_iso)
    else:
        # Candidates exist but no playback yet — record the show with
        # today as started_at so future runs can measure the window.
        started_at = datetime.now(UTC)

    last_playback = datetime.fromisoformat(last_playback_iso) if last_playback_iso else None

    new_state = ShowState(
        show_id=show_id,
        episodes_finished=finished_count,
        episodes_abandoned=abandoned_count,
        episodes_available=episodes_available,
        last_playback_at=last_playback,
        started_at=started_at,
        last_emitted_event=existing.last_emitted_event if existing else None,
        last_emitted_at=existing.last_emitted_at if existing else None,
    )
    q_show_state.upsert(conn, new_state)
    return new_state


def aggregate_all_shows(
    conn: sqlite3.Connection,
    config: TasteConfig,
    *,
    now: datetime | None = None,
) -> list[TasteEvent]:
    """Refresh every show's state and emit any newly-crossed binge events.

    Idempotent: running back-to-back produces zero new rows the second
    time unless playback activity changed the numbers.
    """
    current_now = now or datetime.now(UTC)
    emitted: list[TasteEvent] = []

    show_ids = q_show_state.list_show_ids_with_episodes(conn)
    for show_id in show_ids:
        state = refresh_show_state(conn, show_id)
        if state is None:
            continue
        outcome = evaluate_show(state, config, current_now)
        if outcome.event is None:
            continue
        q_taste_events.insert_event(conn, outcome.event)
        emitted.append(outcome.event)
        # Stamp the idempotence guard.
        state.last_emitted_event = outcome.event.kind
        state.last_emitted_at = outcome.event.timestamp
        q_show_state.upsert(conn, state)

    return emitted


__all__ = [
    "BingeAction",
    "BingeOutcome",
    "aggregate_all_shows",
    "evaluate_show",
    "refresh_show_state",
]
