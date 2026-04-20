"""Pydantic models for HTTP responses + adapters from internal types.

Keeping wire shapes in one module lets CONTRACTS.md reviewers grep
one file, and keeps internal dataclasses (``Candidate``,
``RankedCandidate``, ``ShowState``) free of display-only fields.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel

from archive_agent.state.models import (
    Candidate,
    ContentType,
    RankedCandidate,
)
from archive_agent.state.queries import show_state as q_show_state


class EpisodeInfo(BaseModel):
    season: int
    episode: int
    title: str | None = None
    resume_point_seconds: int | None = None


class RecommendationItem(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None = None
    runtime_minutes: int | None = None
    genres: list[str] = []
    description: str = ""
    poster_url: str
    reasoning: str
    jellyfin_item_id: str | None = None
    # TV-specific
    season: int | None = None
    episode: int | None = None
    episodes_available: int | None = None
    resume_point_seconds: int | None = None


def _poster_url(archive_id: str) -> str:
    """Always return our proxy path. Clients never see upstream URLs."""
    return f"/poster/{archive_id}"


def to_recommendation_item(
    ranked: RankedCandidate, conn: sqlite3.Connection
) -> RecommendationItem:
    """Fold a ``RankedCandidate`` (possibly stale) + the latest DB row
    into the wire format.

    ``ranked.candidate`` is what the ranker saw when it produced the
    pick; the candidate may have since been enriched or gained a
    ``jellyfin_item_id``. Always read fresh fields from ``candidates``
    and fall back to the ranker's snapshot only when there's no row
    (e.g., the candidate got deleted between batch insert and read).
    """
    from archive_agent.state.queries import candidates as q_candidates

    fresh = q_candidates.get_by_archive_id(conn, ranked.candidate.archive_id)
    src: Candidate = fresh or ranked.candidate

    episodes_available: int | None = None
    if src.content_type == ContentType.SHOW and src.show_id is not None:
        state = q_show_state.get(conn, src.show_id)
        episodes_available = state.episodes_available if state else None

    return RecommendationItem(
        archive_id=src.archive_id,
        content_type=src.content_type,
        title=src.title,
        year=src.year,
        runtime_minutes=src.runtime_minutes,
        genres=src.genres,
        description=src.description,
        poster_url=_poster_url(src.archive_id),
        reasoning=ranked.reasoning,
        jellyfin_item_id=src.jellyfin_item_id,
        season=src.season,
        episode=src.episode,
        episodes_available=episodes_available,
        resume_point_seconds=None,
    )


SelectStatus = Literal["ready", "queued", "failed"]


__all__ = [
    "EpisodeInfo",
    "RecommendationItem",
    "SelectStatus",
    "to_recommendation_item",
]
