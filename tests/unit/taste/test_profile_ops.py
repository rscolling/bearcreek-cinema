"""Shared preserve_ids helper — union old+new+events with rating priority."""

from __future__ import annotations

from datetime import UTC, datetime

from archive_agent.state.models import (
    ContentType,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.taste.profile_ops import preserve_ids

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _empty() -> TasteProfile:
    return TasteProfile(version=0, updated_at=_NOW)


def _event(
    kind: TasteEventKind,
    *,
    archive_id: str | None = None,
    show_id: str | None = None,
    source: str = "playback",
) -> TasteEvent:
    return TasteEvent(
        timestamp=_NOW,
        content_type=ContentType.SHOW if show_id else ContentType.MOVIE,
        archive_id=archive_id,
        show_id=show_id,
        kind=kind,
        strength=0.8,
        source=source,
    )


def test_preserves_old_ids_when_llm_drops_them() -> None:
    old = TasteProfile(
        version=1,
        updated_at=_NOW,
        liked_archive_ids=["m1", "m2"],
        liked_show_ids=["s1"],
    )
    new = _empty()  # LLM dropped everything

    result = preserve_ids(old, new, [])

    assert result.liked_archive_ids == ["m1", "m2"]
    assert result.liked_show_ids == ["s1"]


def test_positive_event_moves_id_to_liked() -> None:
    old = TasteProfile(
        version=1,
        updated_at=_NOW,
        disliked_archive_ids=["m1"],  # was disliked
    )
    new = _empty()
    events = [_event(TasteEventKind.REWATCHED, archive_id="m1")]

    result = preserve_ids(old, new, events)

    assert "m1" in result.liked_archive_ids
    assert "m1" not in result.disliked_archive_ids


def test_negative_event_moves_id_to_disliked() -> None:
    old = TasteProfile(
        version=1,
        updated_at=_NOW,
        liked_show_ids=["s1"],
    )
    new = _empty()
    events = [_event(TasteEventKind.BINGE_NEGATIVE, show_id="s1")]

    result = preserve_ids(old, new, events)

    assert "s1" in result.disliked_show_ids
    assert "s1" not in result.liked_show_ids


def test_rated_down_forces_disliked() -> None:
    old = TasteProfile(version=1, updated_at=_NOW, liked_show_ids=["sX"])
    new = TasteProfile(version=2, updated_at=_NOW, liked_show_ids=["sX"])  # LLM still says liked
    events = [_event(TasteEventKind.RATED_DOWN, show_id="sX", source="roku_api")]

    result = preserve_ids(old, new, events)

    assert "sX" in result.disliked_show_ids
    assert "sX" not in result.liked_show_ids


def test_rated_love_forces_liked() -> None:
    old = TasteProfile(version=1, updated_at=_NOW, disliked_show_ids=["sY"])
    new = TasteProfile(version=2, updated_at=_NOW, disliked_show_ids=["sY"])
    events = [_event(TasteEventKind.RATED_LOVE, show_id="sY", source="roku_api")]

    result = preserve_ids(old, new, events)

    assert "sY" in result.liked_show_ids
    assert "sY" not in result.disliked_show_ids


def test_union_of_old_and_new_lists() -> None:
    old = TasteProfile(version=1, updated_at=_NOW, liked_archive_ids=["a", "b"])
    new = TasteProfile(version=2, updated_at=_NOW, liked_archive_ids=["b", "c"])

    result = preserve_ids(old, new, [])

    assert set(result.liked_archive_ids) == {"a", "b", "c"}


def test_neutral_kinds_do_not_move_ids() -> None:
    old = TasteProfile(version=1, updated_at=_NOW, liked_archive_ids=["m1"])
    new = _empty()
    events = [_event(TasteEventKind.DEFERRED, archive_id="m1")]

    result = preserve_ids(old, new, events)

    # DEFERRED is neither positive nor negative; IDs just union from old+new.
    assert "m1" in result.liked_archive_ids
    assert "m1" not in result.disliked_archive_ids
