"""Higher-level history extraction + taste-event classification.

Bootstrap rules for movies are from ARCHITECTURE.md (and echoed in
phase1-04's task card):

- ``play_count >= 2`` AND ``pct >= 90``   → REWATCHED, strength 1.0
- ``play_count >= 1`` AND ``pct >= 90``   → FINISHED, strength 0.7
- ``play_count >= 1`` AND ``pct < 20``    → REJECTED, strength 0.3 (bailed)
- ``play_count == 0``                     → REJECTED, strength 0.2 (never started)
- anything between 20% and 90%            → neutral (no event)

For episodes, raw watches flow into ``episode_watches``; the show-state
aggregator converts them into binge-positive/binge-negative taste events
later (phase3-01). Never-started episodes produce no signal and are
skipped.

Taste events ingested from Jellyfin use ``archive_id = "jellyfin:<uuid>"``
so the Archive.org namespace stays clean — the mapping to real
Archive.org archive_ids happens when discovery matches a Jellyfin-seeded
liked item (phase3).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field

from archive_agent.jellyfin.client import JellyfinClient
from archive_agent.jellyfin.models import JellyfinItem
from archive_agent.state.models import ContentType, EpisodeWatch, TasteEvent, TasteEventKind
from archive_agent.state.queries import episode_watches as q_episodes
from archive_agent.state.queries import taste_events as q_taste

__all__ = [
    "EpisodeWatchRecord",
    "HistoryIngestResult",
    "MovieWatchRecord",
    "classify_movie_signal",
    "fetch_episode_history",
    "fetch_movie_history",
    "ingest_all_history",
]

_TICKS_PER_MINUTE = 60 * 10_000_000  # .NET DateTime ticks — 100 ns each
_ITEM_FIELDS = [
    "UserData",
    "ProductionYear",
    "Genres",
    "RunTimeTicks",
    "Overview",
    "SeriesId",
    "SeriesName",
    "ParentIndexNumber",
    "IndexNumber",
]


class MovieWatchRecord(BaseModel):
    jellyfin_item_id: str
    title: str
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    play_count: int = 0
    played_percentage: float = 0.0  # 0..100 as Jellyfin returns
    last_played_date: datetime | None = None
    runtime_minutes: int | None = None


class EpisodeWatchRecord(BaseModel):
    jellyfin_item_id: str
    series_id: str
    series_name: str | None = None
    season: int
    episode: int
    play_count: int
    played_percentage: float
    last_played_date: datetime | None = None


class HistoryIngestResult(BaseModel):
    movies_seen: int = 0
    movie_events_inserted: int = 0
    movie_events_skipped: int = 0
    episodes_seen: int = 0
    episode_watches_inserted: int = 0
    episode_watches_skipped: int = 0


def _movie_record(item: JellyfinItem) -> MovieWatchRecord:
    ud = item.user_data
    return MovieWatchRecord(
        jellyfin_item_id=item.id,
        title=item.name,
        year=item.production_year,
        genres=item.genres,
        play_count=ud.play_count if ud else 0,
        played_percentage=ud.played_percentage if ud else 0.0,
        last_played_date=ud.last_played_date if ud else None,
        runtime_minutes=(item.run_time_ticks // _TICKS_PER_MINUTE if item.run_time_ticks else None),
    )


def _episode_record(item: JellyfinItem) -> EpisodeWatchRecord | None:
    # Skip specials or malformed rows — we need season + episode numbers
    # and a parent series to place them coherently.
    if not item.series_id or item.parent_index_number is None or item.index_number is None:
        return None
    ud = item.user_data
    return EpisodeWatchRecord(
        jellyfin_item_id=item.id,
        series_id=item.series_id,
        series_name=item.series_name,
        season=item.parent_index_number,
        episode=item.index_number,
        play_count=ud.play_count if ud else 0,
        played_percentage=ud.played_percentage if ud else 0.0,
        last_played_date=ud.last_played_date if ud else None,
    )


async def fetch_movie_history(client: JellyfinClient) -> list[MovieWatchRecord]:
    """Pull every movie in the user's library with UserData attached."""
    records: list[MovieWatchRecord] = []
    async for item in client.list_items_paginated(
        include_item_types=["Movie"], fields=_ITEM_FIELDS
    ):
        records.append(_movie_record(item))
    return records


async def fetch_episode_history(client: JellyfinClient) -> list[EpisodeWatchRecord]:
    """Pull every episode in the user's library with UserData attached.

    Skips specials and items without parent-series linkage; they don't
    contribute usefully to the show-state aggregator.
    """
    records: list[EpisodeWatchRecord] = []
    async for item in client.list_items_paginated(
        include_item_types=["Episode"], fields=_ITEM_FIELDS
    ):
        rec = _episode_record(item)
        if rec is not None:
            records.append(rec)
    return records


def classify_movie_signal(record: MovieWatchRecord) -> TasteEvent | None:
    """Bootstrap-rule classifier. See the module docstring."""
    pct = record.played_percentage
    plays = record.play_count
    timestamp = record.last_played_date or datetime.now(UTC)
    archive_id = f"jellyfin:{record.jellyfin_item_id}"

    def _event(kind: TasteEventKind, strength: float) -> TasteEvent:
        return TasteEvent(
            timestamp=timestamp,
            content_type=ContentType.MOVIE,
            archive_id=archive_id,
            kind=kind,
            strength=strength,
            source="bootstrap",
        )

    if plays >= 2 and pct >= 90:
        return _event(TasteEventKind.REWATCHED, 1.0)
    if plays >= 1 and pct >= 90:
        return _event(TasteEventKind.FINISHED, 0.7)
    if plays >= 1 and pct < 20:
        return _event(TasteEventKind.REJECTED, 0.3)
    if plays == 0:
        return _event(TasteEventKind.REJECTED, 0.2)
    return None


async def ingest_all_history(
    client: JellyfinClient,
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> HistoryIngestResult:
    """Fetch + classify + persist. Idempotent.

    Movies: bootstrap taste events keyed on ``(archive_id, kind, source)``
    are skipped if already present.
    Episodes: raw watches dedupe via a unique index on
    ``(jellyfin_item_id, timestamp)`` — ``INSERT`` raises
    ``IntegrityError``, which we catch and count as skipped.
    """
    result = HistoryIngestResult()
    log = structlog.get_logger(component="jellyfin")

    for movie in await fetch_movie_history(client):
        result.movies_seen += 1
        event = classify_movie_signal(movie)
        if event is None:
            result.movie_events_skipped += 1
            continue
        if dry_run:
            result.movie_events_inserted += 1
            continue
        cur = conn.execute(
            "SELECT 1 FROM taste_events WHERE archive_id = ? AND kind = ? AND source = ? LIMIT 1",
            (event.archive_id, event.kind.value, event.source),
        )
        if cur.fetchone() is not None:
            result.movie_events_skipped += 1
            continue
        q_taste.insert_event(conn, event)
        result.movie_events_inserted += 1

    for ep in await fetch_episode_history(client):
        result.episodes_seen += 1
        if ep.play_count == 0:
            # Never-started episodes are noise — the aggregator only
            # looks at actual plays.
            result.episode_watches_skipped += 1
            continue
        watch = EpisodeWatch(
            timestamp=ep.last_played_date or datetime.now(UTC),
            show_id=f"jellyfin:{ep.series_id}",
            season=ep.season,
            episode=ep.episode,
            completion_pct=ep.played_percentage / 100.0,
            jellyfin_item_id=ep.jellyfin_item_id,
        )
        if dry_run:
            result.episode_watches_inserted += 1
            continue
        try:
            q_episodes.insert_watch(conn, watch)
            result.episode_watches_inserted += 1
        except sqlite3.IntegrityError:
            result.episode_watches_skipped += 1

    log.info("jellyfin_history_ingested", **result.model_dump())
    return result
