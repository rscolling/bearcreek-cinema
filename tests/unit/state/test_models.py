"""Validation behavior of the Pydantic state models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from archive_agent.state.models import (
    Candidate,
    ContentType,
    EpisodeWatch,
    EraPreference,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)


def _now() -> datetime:
    return datetime.now(UTC)


def test_candidate_happy_path() -> None:
    c = Candidate(
        archive_id="sita_sings_the_blues",
        content_type=ContentType.MOVIE,
        title="Sita Sings the Blues",
        source_collection="moviesandfilms",
        discovered_at=_now(),
    )
    assert c.genres == []
    assert c.status.value == "new"


def test_taste_event_rejects_episode_content_type() -> None:
    with pytest.raises(ValidationError) as exc:
        TasteEvent(
            timestamp=_now(),
            content_type=ContentType.EPISODE,
            archive_id="anything",
            kind=TasteEventKind.FINISHED,
            strength=1.0,
        )
    assert "EPISODE" in str(exc.value)


def test_taste_event_requires_archive_or_show_id() -> None:
    with pytest.raises(ValidationError) as exc:
        TasteEvent(
            timestamp=_now(),
            content_type=ContentType.MOVIE,
            kind=TasteEventKind.FINISHED,
            strength=0.9,
        )
    assert "archive_id" in str(exc.value) or "show_id" in str(exc.value)


def test_taste_event_strength_bounded() -> None:
    with pytest.raises(ValidationError):
        TasteEvent(
            timestamp=_now(),
            content_type=ContentType.MOVIE,
            archive_id="x",
            kind=TasteEventKind.FINISHED,
            strength=1.5,
        )


def test_era_preference_weight_bounded() -> None:
    EraPreference(decade=1940, weight=-1.0)
    EraPreference(decade=1940, weight=1.0)
    with pytest.raises(ValidationError):
        EraPreference(decade=1940, weight=1.5)


def test_taste_profile_defaults_are_independent() -> None:
    p1 = TasteProfile(version=1, updated_at=_now())
    p2 = TasteProfile(version=2, updated_at=_now())
    p1.liked_genres.append("noir")
    assert p2.liked_genres == []


def test_episode_watch_completion_bounded() -> None:
    EpisodeWatch(
        timestamp=_now(),
        show_id="sh",
        season=1,
        episode=1,
        completion_pct=0.0,
        jellyfin_item_id="jid",
    )
    with pytest.raises(ValidationError):
        EpisodeWatch(
            timestamp=_now(),
            show_id="sh",
            season=1,
            episode=1,
            completion_pct=1.5,
            jellyfin_item_id="jid",
        )
