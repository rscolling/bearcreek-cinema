"""Classification + ingestion logic. No network — uses a fake client."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from archive_agent.jellyfin.history import (
    MovieWatchRecord,
    classify_movie_signal,
    ingest_all_history,
)
from archive_agent.jellyfin.models import JellyfinItem, JellyfinItemPage
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending
from archive_agent.state.models import TasteEventKind


def _movie(**kw: Any) -> MovieWatchRecord:
    defaults = dict(
        jellyfin_item_id="id-1",
        title="A Movie",
        play_count=0,
        played_percentage=0.0,
    )
    defaults.update(kw)
    return MovieWatchRecord(**defaults)


def test_classify_rewatched() -> None:
    event = classify_movie_signal(_movie(play_count=3, played_percentage=100.0))
    assert event is not None
    assert event.kind is TasteEventKind.REWATCHED
    assert event.strength == 1.0
    assert event.archive_id == "jellyfin:id-1"
    assert event.source == "bootstrap"


def test_classify_finished_once() -> None:
    event = classify_movie_signal(_movie(play_count=1, played_percentage=97.3))
    assert event is not None
    assert event.kind is TasteEventKind.FINISHED
    assert event.strength == pytest.approx(0.7)


def test_classify_bailed_early() -> None:
    event = classify_movie_signal(_movie(play_count=1, played_percentage=18.2))
    assert event is not None
    assert event.kind is TasteEventKind.REJECTED
    assert event.strength == pytest.approx(0.3)


def test_classify_never_played() -> None:
    event = classify_movie_signal(_movie(play_count=0, played_percentage=0.0))
    assert event is not None
    assert event.kind is TasteEventKind.REJECTED
    assert event.strength == pytest.approx(0.2)


def test_classify_midway_returns_none() -> None:
    assert classify_movie_signal(_movie(play_count=1, played_percentage=55.0)) is None
    assert classify_movie_signal(_movie(play_count=1, played_percentage=89.0)) is None


def test_classify_uses_last_played_when_present() -> None:
    when = datetime.now(UTC) - timedelta(days=30)
    event = classify_movie_signal(
        _movie(play_count=1, played_percentage=95.0, last_played_date=when)
    )
    assert event is not None
    assert event.timestamp == when


# --- ingest_all_history via a fake client ------------------------------------


class _FakeJellyfinClient:
    """Minimal drop-in for ``JellyfinClient`` that replays a fixture."""

    def __init__(self, items: list[JellyfinItem]) -> None:
        self._items = items

    async def list_items_paginated(
        self,
        *,
        include_item_types: list[str] | None = None,
        fields: list[str] | None = None,
        library_id: str | None = None,
        filters: list[str] | None = None,
        page_size: int = 200,
    ) -> AsyncIterator[JellyfinItem]:
        wanted = set(include_item_types or [])
        for it in self._items:
            if not wanted or it.type in wanted:
                yield it


@pytest.fixture
def db(sample_history_json: dict[str, Any]) -> sqlite3.Connection:
    conn = connect(":memory:")
    apply_pending(conn)
    return conn


async def test_ingest_writes_expected_counts(
    sample_history_json: dict[str, Any], db: sqlite3.Connection
) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    fake = _FakeJellyfinClient(page.items)
    result = await ingest_all_history(fake, db)  # type: ignore[arg-type]

    # Fixture: 4 movies (pct 100/97/18/0), 3 episodes (all played: 100/100/88)
    assert result.movies_seen == 4
    # 3 of 4 movies produce an event; the 55-97 range yielded none in this
    # fixture — Meet John Doe is pct=0 so it rejects.
    assert result.movie_events_inserted == 4  # Third Man, His Girl, Carnival, Meet John Doe
    assert result.episodes_seen == 3
    assert result.episode_watches_inserted == 3


async def test_ingest_is_idempotent(
    sample_history_json: dict[str, Any], db: sqlite3.Connection
) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    fake = _FakeJellyfinClient(page.items)
    first = await ingest_all_history(fake, db)  # type: ignore[arg-type]
    second = await ingest_all_history(fake, db)  # type: ignore[arg-type]
    assert first.movie_events_inserted == 4
    assert first.episode_watches_inserted == 3
    assert second.movie_events_inserted == 0
    assert second.movie_events_skipped == 4
    assert second.episode_watches_inserted == 0
    assert second.episode_watches_skipped == 3


async def test_dry_run_writes_nothing(
    sample_history_json: dict[str, Any], db: sqlite3.Connection
) -> None:
    page = JellyfinItemPage.model_validate(sample_history_json)
    fake = _FakeJellyfinClient(page.items)
    result = await ingest_all_history(fake, db, dry_run=True)  # type: ignore[arg-type]
    assert result.movie_events_inserted == 4
    # Nothing should actually be in the DB
    count = db.execute("SELECT COUNT(*) FROM taste_events").fetchone()[0]
    assert count == 0
    count = db.execute("SELECT COUNT(*) FROM episode_watches").fetchone()[0]
    assert count == 0
