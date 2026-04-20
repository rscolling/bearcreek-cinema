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


def to_recommendation_item(ranked: RankedCandidate, conn: sqlite3.Connection) -> RecommendationItem:
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


SearchResultStatus = Literal["ready", "downloadable", "discoverable"]


class SearchResultItem(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None = None
    poster_url: str
    status: SearchResultStatus
    jellyfin_item_id: str | None = None
    runtime_minutes: int | None = None
    next_episode: EpisodeInfo | None = None
    relevance_score: float
    match_reason: str


class AutocompleteSuggestion(BaseModel):
    title: str
    archive_id: str


_DOWNLOADABLE_STATUSES = {"new", "ranked", "approved"}


def _search_status_for(cand: Candidate) -> SearchResultStatus:
    if cand.jellyfin_item_id is not None:
        return "ready"
    if cand.status.value in _DOWNLOADABLE_STATUSES:
        return "downloadable"
    # Terminal statuses (rejected / expired / downloaded / committed) —
    # treat as downloadable so the UI lets the user re-trigger. They
    # can't be "discoverable" (that's the live-archive fallback).
    return "downloadable"


def to_search_result_item(
    cand: Candidate,
    score: float,
    *,
    match_reason: str,
    next_episode: EpisodeInfo | None = None,
) -> SearchResultItem:
    return SearchResultItem(
        archive_id=cand.archive_id,
        content_type=cand.content_type,
        title=cand.title,
        year=cand.year,
        poster_url=_poster_url(cand.archive_id),
        status=_search_status_for(cand),
        jellyfin_item_id=cand.jellyfin_item_id,
        runtime_minutes=cand.runtime_minutes,
        next_episode=next_episode,
        relevance_score=score,
        match_reason=match_reason,
    )


__all__ = [
    "AutocompleteSuggestion",
    "EpisodeInfo",
    "RecommendationItem",
    "SearchResultItem",
    "SearchResultStatus",
    "SelectStatus",
    "to_recommendation_item",
    "to_search_result_item",
]
