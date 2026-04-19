"""Parsing of Jellyfin response JSON into our Pydantic models."""

from __future__ import annotations

from typing import Any

from archive_agent.jellyfin.models import (
    JellyfinItem,
    JellyfinItemPage,
    JellyfinUser,
)


def test_item_page_round_trip(sample_history_json: dict[str, Any]) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    assert page.total_record_count == 7
    assert len(page.items) == 7


def test_movie_parses_with_user_data(sample_history_json: dict[str, Any]) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    third_man = next(i for i in page.items if i.name == "The Third Man")
    assert third_man.type == "Movie"
    assert third_man.production_year == 1949
    assert third_man.genres == ["Film-Noir", "Mystery", "Thriller"]
    assert third_man.user_data is not None
    assert third_man.user_data.play_count == 2
    assert third_man.user_data.played_percentage == 100.0
    assert third_man.user_data.played is True


def test_episode_parses_season_and_episode(sample_history_json: dict[str, Any]) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    episode = next(i for i in page.items if i.type == "Episode")
    assert episode.series_id is not None
    assert episode.parent_index_number is not None  # season
    assert episode.index_number is not None  # episode


def test_extra_fields_are_ignored() -> None:
    """Jellyfin may add fields; our models must tolerate them."""
    raw = {
        "Id": "x",
        "Name": "y",
        "Type": "Movie",
        "FutureFieldThatDoesNotExistYet": "noise",
        "UserData": {
            "PlayedPercentage": 50.0,
            "PlayCount": 1,
            "SomeNewField": 42,
        },
    }
    item = JellyfinItem.model_validate(raw)
    assert item.id == "x"
    assert item.user_data is not None
    assert item.user_data.play_count == 1


def test_user_parses_policy_nested() -> None:
    raw = {
        "Id": "u1",
        "Name": "colling",
        "LastActivityDate": "2026-04-19T03:14:13.7728362Z",
        "Policy": {"IsAdministrator": True, "OtherFlag": False},
    }
    user = JellyfinUser.model_validate(raw)
    assert user.name == "colling"
    assert user.policy.is_administrator is True
