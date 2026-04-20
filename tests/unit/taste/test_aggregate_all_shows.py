"""End-to-end aggregator: fixture DB, simulated playback, assert events."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from archive_agent.config import TasteConfig
from archive_agent.state.models import Candidate, ContentType, EpisodeWatch
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import episode_watches as q_watches
from archive_agent.state.queries import show_state as q_show_state
from archive_agent.state.queries import taste_events as q_taste_events
from archive_agent.taste.aggregator import aggregate_all_shows, refresh_show_state


def _seed_episodes(conn: sqlite3.Connection, show_id: str, n: int) -> None:
    now = datetime.now(UTC)
    for i in range(1, n + 1):
        q_candidates.upsert_candidate(
            conn,
            Candidate(
                archive_id=f"{show_id}_s01e{i:02d}",
                content_type=ContentType.EPISODE,
                title=f"{show_id} S01E{i:02d}",
                show_id=show_id,
                season=1,
                episode=i,
                source_collection="television",
                discovered_at=now,
            ),
        )


def _record_watch(
    conn: sqlite3.Connection,
    show_id: str,
    season: int,
    episode: int,
    completion: float,
    at: datetime,
) -> None:
    q_watches.insert_watch(
        conn,
        EpisodeWatch(
            timestamp=at,
            show_id=show_id,
            season=season,
            episode=episode,
            completion_pct=completion,
            jellyfin_item_id=f"jf_{show_id}_{season}_{episode}",
        ),
    )


def test_positive_binge_emits_one_event(db: sqlite3.Connection, taste_config: TasteConfig) -> None:
    _seed_episodes(db, "showA", 8)
    now = datetime.now(UTC)
    # Finish 6 of 8 episodes over 10 days — crosses 75%/60d.
    for i in range(1, 7):
        _record_watch(db, "showA", 1, i, 0.95, now - timedelta(days=10 - i))

    events = aggregate_all_shows(db, taste_config, now=now)

    assert len(events) == 1
    assert events[0].show_id == "showA"
    assert events[0].kind.value == "binge_positive"

    # Idempotent: second run produces nothing new.
    events2 = aggregate_all_shows(db, taste_config, now=now)
    assert events2 == []

    # Show state stamped.
    state = q_show_state.get(db, "showA")
    assert state is not None
    assert state.last_emitted_event is not None
    assert state.last_emitted_event.value == "binge_positive"


def test_negative_emits_after_inactivity(
    db: sqlite3.Connection, taste_config: TasteConfig
) -> None:
    _seed_episodes(db, "showB", 10)
    now = datetime.now(UTC)
    # Watch 1 episode 60 days ago, nothing since.
    _record_watch(db, "showB", 1, 1, 0.95, now - timedelta(days=60))

    events = aggregate_all_shows(db, taste_config, now=now)

    assert len(events) == 1
    assert events[0].kind.value == "binge_negative"


def test_partial_watch_does_not_count_as_finished(
    db: sqlite3.Connection, taste_config: TasteConfig
) -> None:
    _seed_episodes(db, "showC", 8)
    now = datetime.now(UTC)
    # Watched 6 episodes to 50% completion each — none count as finished.
    for i in range(1, 7):
        _record_watch(db, "showC", 1, i, 0.5, now - timedelta(days=10 - i))

    events = aggregate_all_shows(db, taste_config, now=now)

    # Not positive (pct=0) and not negative yet (still active).
    assert events == []
    state = q_show_state.get(db, "showC")
    assert state is not None
    assert state.episodes_finished == 0
    assert state.episodes_abandoned == 6


def test_refresh_respects_finished_max_per_episode(db: sqlite3.Connection) -> None:
    """Repeated rewatches collapse to one finished episode."""
    _seed_episodes(db, "showD", 4)
    now = datetime.now(UTC)
    # Same episode, three partial watches then a finish.
    _record_watch(db, "showD", 1, 1, 0.2, now - timedelta(days=10))
    _record_watch(db, "showD", 1, 1, 0.5, now - timedelta(days=5))
    _record_watch(db, "showD", 1, 1, 0.95, now - timedelta(days=1))

    state = refresh_show_state(db, "showD")

    assert state is not None
    assert state.episodes_finished == 1
    assert state.episodes_abandoned == 0


def test_refresh_preserves_last_emitted(
    db: sqlite3.Connection, taste_config: TasteConfig
) -> None:
    _seed_episodes(db, "showE", 8)
    now = datetime.now(UTC)
    for i in range(1, 7):
        _record_watch(db, "showE", 1, i, 0.95, now - timedelta(days=10 - i))

    aggregate_all_shows(db, taste_config, now=now)
    # Add another watch after emission; re-refresh.
    _record_watch(db, "showE", 1, 7, 0.95, now - timedelta(hours=1))
    state = refresh_show_state(db, "showE")

    assert state is not None
    assert state.last_emitted_event is not None
    assert state.last_emitted_event.value == "binge_positive"


def test_ignores_rating_events_when_checking_binge_idempotence(
    db: sqlite3.Connection, taste_config: TasteConfig
) -> None:
    """ADR-013 rating events must not interfere with binge aggregation."""
    from archive_agent.state.models import TasteEvent, TasteEventKind

    _seed_episodes(db, "showF", 8)
    now = datetime.now(UTC)
    # A user rated the show 👍 before any binge tracking.
    q_taste_events.insert_event(
        db,
        TasteEvent(
            timestamp=now - timedelta(days=100),
            content_type=ContentType.SHOW,
            show_id="showF",
            kind=TasteEventKind.RATED_UP,
            strength=0.6,
            source="roku_api",
        ),
    )
    for i in range(1, 7):
        _record_watch(db, "showF", 1, i, 0.95, now - timedelta(days=10 - i))

    events = aggregate_all_shows(db, taste_config, now=now)

    # The rating event doesn't short-circuit binge detection.
    binge_events = [e for e in events if e.kind.value.startswith("binge_")]
    assert len(binge_events) == 1
    assert binge_events[0].kind.value == "binge_positive"


@pytest.mark.parametrize("episodes", [0, 4])
def test_empty_or_trivial_show_skipped(
    db: sqlite3.Connection, taste_config: TasteConfig, episodes: int
) -> None:
    if episodes:
        _seed_episodes(db, "showG", episodes)
    # No watches at all — aggregator should produce no events regardless
    # of whether candidates exist.
    events = aggregate_all_shows(db, taste_config)
    assert events == []
