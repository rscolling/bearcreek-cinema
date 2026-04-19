"""Pydantic models for TMDb v3 responses.

Only fields the agent reads are modelled; ``extra='ignore'`` keeps us
resilient to TMDb adding new fields. Search endpoints return a
different shape than by-id endpoints — notably, search returns
``genre_ids: list[int]`` while ``/movie/{id}`` returns
``genres: list[{id, name}]``. Both shapes coexist here and
``enrich_candidate`` prefers the resolved names when available.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TmdbConfiguration",
    "TmdbGenre",
    "TmdbMovie",
    "TmdbShow",
]


class _TmdbModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TmdbGenre(_TmdbModel):
    id: int
    name: str


class TmdbMovie(_TmdbModel):
    id: int
    title: str
    release_date: str | None = None
    runtime: int | None = None  # minutes; None in search, int in by-id
    overview: str = ""
    poster_path: str | None = None
    genres: list[TmdbGenre] = Field(default_factory=list)  # in by-id responses
    genre_ids: list[int] = Field(default_factory=list)  # in search responses

    @property
    def year(self) -> int | None:
        return _year_from_date(self.release_date)


class TmdbShow(_TmdbModel):
    id: int
    name: str
    first_air_date: str | None = None
    episode_run_time: list[int] = Field(default_factory=list)
    overview: str = ""
    poster_path: str | None = None
    genres: list[TmdbGenre] = Field(default_factory=list)
    genre_ids: list[int] = Field(default_factory=list)

    @property
    def year(self) -> int | None:
        return _year_from_date(self.first_air_date)


class TmdbConfiguration(_TmdbModel):
    """Flattened view of TMDb's ``/configuration`` response.

    Built from the nested ``{"images": {...}}`` shape via
    :meth:`from_api` so callers can use ``config.images_base_url``
    directly without walking ``.images.secure_base_url`` everywhere.
    """

    images_base_url: str = ""
    poster_sizes: list[str] = Field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TmdbConfiguration:
        images = data.get("images") or {}
        return cls(
            images_base_url=str(images.get("secure_base_url") or ""),
            poster_sizes=list(images.get("poster_sizes") or []),
        )


def _year_from_date(date: str | None) -> int | None:
    """TMDb uses ISO ``YYYY-MM-DD`` but occasionally empty strings."""
    if not date or len(date) < 4:
        return None
    try:
        return int(date[:4])
    except ValueError:
        return None
