"""Explicit 3-thumb show ratings (ADR-013).

Ratings are inserted into ``taste_events`` by the Roku write path
(phase 5) with ``source='roku_api'`` and a content_type of SHOW. This
module is the **read side**: latest-wins lookups used by the ranker
and profile updater.

Rating events are append-only — a user who flips 👎 → 👍 → 👎 leaves
three rows, and the newest one reflects current intent.
"""

from __future__ import annotations

import sqlite3

from archive_agent.state.models import TasteEvent, TasteEventKind
from archive_agent.state.queries import taste_events as q_taste_events

RATING_KINDS: frozenset[TasteEventKind] = frozenset(
    {
        TasteEventKind.RATED_DOWN,
        TasteEventKind.RATED_UP,
        TasteEventKind.RATED_LOVE,
    }
)


def latest_for_show(conn: sqlite3.Connection, show_id: str) -> TasteEvent | None:
    """Return the newest ``roku_api`` rating event for this show.

    ``None`` when the show is unrated. Non-Roku sources (e.g.,
    ``playback`` or ``bootstrap``) are ignored — only explicit thumbs
    count as ratings.
    """
    return q_taste_events.latest_rating_for_show(conn, show_id)


def latest_for_all_shows(conn: sqlite3.Connection) -> dict[str, TasteEvent]:
    """Return ``{show_id: latest_rating_event}`` for every rated show.

    One-shot bulk read — avoids N+1 queries in the rank hot path.
    Unrated shows are simply absent from the dict.
    """
    return q_taste_events.latest_rating_per_show(conn)


__all__ = [
    "RATING_KINDS",
    "latest_for_all_shows",
    "latest_for_show",
]
