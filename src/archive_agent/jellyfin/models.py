"""Pydantic models for Jellyfin REST responses.

Only fields the agent actually reads are modelled; ``extra='ignore'``
means upstream schema additions don't crash the client. Aliases keep
snake_case in Python and PascalCase on the wire.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _JellyfinModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class JellyfinServerInfo(_JellyfinModel):
    id: str = Field(alias="Id")
    server_name: str = Field(alias="ServerName")
    version: str = Field(alias="Version")
    product_name: str = Field(alias="ProductName")


class JellyfinUserPolicy(_JellyfinModel):
    is_administrator: bool = Field(alias="IsAdministrator", default=False)


class JellyfinUser(_JellyfinModel):
    id: str = Field(alias="Id")
    name: str = Field(alias="Name")
    last_activity_date: datetime | None = Field(alias="LastActivityDate", default=None)
    policy: JellyfinUserPolicy = Field(alias="Policy", default_factory=JellyfinUserPolicy)


class JellyfinLibrary(_JellyfinModel):
    id: str = Field(alias="Id")
    name: str = Field(alias="Name")
    collection_type: str | None = Field(alias="CollectionType", default=None)


class JellyfinUserData(_JellyfinModel):
    played_percentage: float = Field(alias="PlayedPercentage", default=0.0)
    play_count: int = Field(alias="PlayCount", default=0)
    last_played_date: datetime | None = Field(alias="LastPlayedDate", default=None)
    played: bool = Field(alias="Played", default=False)
    playback_position_ticks: int = Field(alias="PlaybackPositionTicks", default=0)
    is_favorite: bool = Field(alias="IsFavorite", default=False)


class JellyfinItem(_JellyfinModel):
    id: str = Field(alias="Id")
    name: str = Field(alias="Name")
    type: str = Field(alias="Type")  # Movie | Episode | Series | Season | ...
    production_year: int | None = Field(alias="ProductionYear", default=None)
    run_time_ticks: int | None = Field(alias="RunTimeTicks", default=None)
    genres: list[str] = Field(alias="Genres", default_factory=list)
    overview: str | None = Field(alias="Overview", default=None)
    user_data: JellyfinUserData | None = Field(alias="UserData", default=None)
    # Episode fields
    series_id: str | None = Field(alias="SeriesId", default=None)
    series_name: str | None = Field(alias="SeriesName", default=None)
    season_name: str | None = Field(alias="SeasonName", default=None)
    parent_index_number: int | None = Field(alias="ParentIndexNumber", default=None)
    index_number: int | None = Field(alias="IndexNumber", default=None)


class JellyfinItemPage(_JellyfinModel):
    items: list[JellyfinItem] = Field(alias="Items", default_factory=list)
    total_record_count: int = Field(alias="TotalRecordCount", default=0)
    start_index: int = Field(alias="StartIndex", default=0)


__all__ = [
    "JellyfinItem",
    "JellyfinItemPage",
    "JellyfinLibrary",
    "JellyfinServerInfo",
    "JellyfinUser",
    "JellyfinUserData",
    "JellyfinUserPolicy",
]
