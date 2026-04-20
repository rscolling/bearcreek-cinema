"""Pydantic models for the state DB (CONTRACTS.md §1).

These models are the single source of truth for the data the agent stores
and exchanges. Other modules import from here; they do not redefine
fields. Enum values are the canonical string forms used in SQL rows and
in API JSON.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ContentType(StrEnum):
    MOVIE = "movie"
    SHOW = "show"
    EPISODE = "episode"


class CandidateStatus(StrEnum):
    NEW = "new"
    RANKED = "ranked"
    APPROVED = "approved"
    SAMPLING = "sampling"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    COMMITTED = "committed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Candidate(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None = None
    runtime_minutes: int | None = None
    show_id: str | None = None
    season: int | None = None
    episode: int | None = None
    total_episodes_known: int | None = None
    genres: list[str] = Field(default_factory=list)
    description: str = ""
    poster_url: str | None = None
    formats_available: list[str] = Field(default_factory=list)
    size_bytes: int | None = None
    source_collection: Literal["moviesandfilms", "television"]
    status: CandidateStatus = CandidateStatus.NEW
    discovered_at: datetime
    jellyfin_item_id: str | None = None


class TasteEventKind(StrEnum):
    # Implicit signals — derived from playback behavior.
    FINISHED = "finished"
    ABANDONED = "abandoned"
    REWATCHED = "rewatched"
    BINGE_POSITIVE = "binge_positive"
    BINGE_NEGATIVE = "binge_negative"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    # Explicit 3-thumb show ratings — see ADR-013. Always
    # content_type=SHOW, source="roku_api". Latest thumb per show wins;
    # history is kept append-only for audit.
    RATED_DOWN = "rated_down"     # strength 0.9 — strong negative
    RATED_UP = "rated_up"         # strength 0.6
    RATED_LOVE = "rated_love"     # strength 1.0 — max positive


class TasteEvent(BaseModel):
    id: int | None = None
    timestamp: datetime
    content_type: ContentType  # MOVIE or SHOW — never EPISODE
    archive_id: str | None = None
    show_id: str | None = None
    kind: TasteEventKind
    strength: float = Field(ge=0.0, le=1.0)
    source: Literal["playback", "roku_api", "bootstrap"] = "playback"

    @field_validator("content_type")
    @classmethod
    def _not_episode(cls, v: ContentType) -> ContentType:
        # Episode-level events update resume state, not the taste profile
        # (see ARCHITECTURE.md §"Unified taste profile").
        if v is ContentType.EPISODE:
            raise ValueError("TasteEvent.content_type must be MOVIE or SHOW, not EPISODE")
        return v

    @model_validator(mode="after")
    def _one_of_ids(self) -> TasteEvent:
        if self.archive_id is None and self.show_id is None:
            raise ValueError("TasteEvent requires either archive_id (movie) or show_id (show)")
        return self


class EpisodeWatch(BaseModel):
    """Raw episode playback event. Does NOT feed the taste profile directly;
    only the show-state aggregator reads these."""

    id: int | None = None
    timestamp: datetime
    show_id: str
    season: int
    episode: int
    completion_pct: float = Field(ge=0.0, le=1.0)
    jellyfin_item_id: str


class ShowState(BaseModel):
    show_id: str
    episodes_finished: int = 0
    episodes_abandoned: int = 0
    episodes_available: int = 0
    last_playback_at: datetime | None = None
    started_at: datetime
    last_emitted_event: TasteEventKind | None = None
    last_emitted_at: datetime | None = None


class EraPreference(BaseModel):
    decade: int  # e.g., 1940 for the 1940s
    weight: float = Field(ge=-1.0, le=1.0)


class TasteProfile(BaseModel):
    version: int  # monotonic
    updated_at: datetime
    liked_genres: list[str] = Field(default_factory=list)
    disliked_genres: list[str] = Field(default_factory=list)
    era_preferences: list[EraPreference] = Field(default_factory=list)
    runtime_tolerance_minutes: int = 150
    liked_archive_ids: list[str] = Field(default_factory=list)
    liked_show_ids: list[str] = Field(default_factory=list)
    disliked_archive_ids: list[str] = Field(default_factory=list)
    disliked_show_ids: list[str] = Field(default_factory=list)
    summary: str = ""  # LLM-maintained prose, ~300 words


class RankedCandidate(BaseModel):
    candidate: Candidate
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    rank: int = Field(ge=1)


class SearchFilter(BaseModel):
    content_types: list[ContentType] | None = None
    genres: list[str] | None = None
    max_runtime_minutes: int | None = None
    episode_length_range: tuple[int, int] | None = None
    era: tuple[int, int] | None = None
    keywords: list[str] = Field(default_factory=list)


__all__ = [
    "Candidate",
    "CandidateStatus",
    "ContentType",
    "EpisodeWatch",
    "EraPreference",
    "RankedCandidate",
    "SearchFilter",
    "ShowState",
    "TasteEvent",
    "TasteEventKind",
    "TasteProfile",
]
