"""TFIDFProvider: rank + update_profile + parse_search (phase3-06)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from archive_agent.ranking.tfidf_provider import TFIDFProvider
from archive_agent.state.models import (
    Candidate,
    ContentType,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(
    archive_id: str,
    title: str,
    *,
    genres: list[str] | None = None,
    show_id: str | None = None,
    content_type: ContentType = ContentType.MOVIE,
    year: int = 1950,
    runtime: int | None = 95,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        runtime_minutes=runtime,
        genres=genres or ["Noir"],
        show_id=show_id,
        source_collection="moviesandfilms" if content_type == ContentType.MOVIE else "television",
        discovered_at=_NOW,
    )


def _profile(liked_genres: list[str] | None = None, **kwargs: Any) -> TasteProfile:
    return TasteProfile(
        version=1,
        updated_at=_NOW,
        liked_genres=liked_genres or [],
        **kwargs,
    )


# --- rank -------------------------------------------------------------------


async def test_rank_returns_n_picks(db: sqlite3.Connection) -> None:
    items = [_candidate(f"m{i}", f"Film {i}", genres=["Noir"]) for i in range(6)]
    for c in items:
        q_candidates.upsert_candidate(db, c)
    provider = TFIDFProvider(conn=db)

    picks = await provider.rank(_profile(liked_genres=["Noir"]), items, n=3)

    assert len(picks) == 3
    assert all(p.reasoning.startswith("TF-IDF:") for p in picks)
    assert [p.rank for p in picks] == [1, 2, 3]


async def test_rank_empty_candidates_returns_empty(db: sqlite3.Connection) -> None:
    provider = TFIDFProvider(conn=db)
    assert await provider.rank(_profile(), [], n=5) == []


async def test_rated_love_boosts_same_show(db: sqlite3.Connection) -> None:
    # Two near-identical candidates; the one tied to a RATED_LOVE show wins.
    for c in [
        _candidate("e1", "Show A E1", show_id="showA", content_type=ContentType.EPISODE),
        _candidate("e2", "Show B E1", show_id="showB", content_type=ContentType.EPISODE),
    ]:
        q_candidates.upsert_candidate(db, c)

    provider = TFIDFProvider(conn=db)
    cands = [
        _candidate("e1", "Show A E1", show_id="showA", content_type=ContentType.EPISODE),
        _candidate("e2", "Show B E1", show_id="showB", content_type=ContentType.EPISODE),
    ]
    ratings = {
        "showA": TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.SHOW,
            show_id="showA",
            kind=TasteEventKind.RATED_LOVE,
            strength=1.0,
            source="roku_api",
        )
    }

    picks = await provider.rank(_profile(liked_genres=["Noir"]), cands, n=2, ratings=ratings)

    assert picks[0].candidate.show_id == "showA"


async def test_rated_down_excludes_show(db: sqlite3.Connection) -> None:
    # Single candidate, RATED_DOWN — score drops below zero, filtered.
    cand = _candidate("e1", "Bad Show E1", show_id="showBad", content_type=ContentType.EPISODE)
    q_candidates.upsert_candidate(db, cand)
    provider = TFIDFProvider(conn=db)
    ratings = {
        "showBad": TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.SHOW,
            show_id="showBad",
            kind=TasteEventKind.RATED_DOWN,
            strength=0.9,
            source="roku_api",
        )
    }

    picks = await provider.rank(_profile(liked_genres=["Noir"]), [cand], n=1, ratings=ratings)

    assert picks == []


# --- update_profile ---------------------------------------------------------


async def test_update_profile_increments_version_and_preserves_ids(
    db: sqlite3.Connection,
) -> None:
    provider = TFIDFProvider(conn=db)
    current = TasteProfile(
        version=3,
        updated_at=_NOW,
        liked_archive_ids=["m_old"],
        liked_genres=["Drama"],
    )
    events = [
        TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.MOVIE,
            archive_id="m_new",
            kind=TasteEventKind.FINISHED,
            strength=0.9,
            source="playback",
        )
    ]

    updated = await provider.update_profile(current, events)

    assert updated.version == 4
    assert "m_old" in updated.liked_archive_ids
    assert "m_new" in updated.liked_archive_ids
    assert updated.summary.startswith("TF-IDF profile")


async def test_update_profile_tallies_genres_from_candidates(
    db: sqlite3.Connection,
) -> None:
    # Seed two finished movies — genres drive the tally.
    q_candidates.upsert_candidate(
        db, _candidate("m1", "Film 1", genres=["Noir", "Thriller"])
    )
    q_candidates.upsert_candidate(
        db, _candidate("m2", "Film 2", genres=["Noir"])
    )
    events = [
        TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.MOVIE,
            archive_id="m1",
            kind=TasteEventKind.FINISHED,
            strength=0.9,
        ),
        TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.MOVIE,
            archive_id="m2",
            kind=TasteEventKind.FINISHED,
            strength=0.9,
        ),
    ]
    provider = TFIDFProvider(conn=db)

    updated = await provider.update_profile(
        TasteProfile(version=0, updated_at=_NOW), events
    )

    # Noir has 2 tallies, Thriller has 1 → Noir first.
    assert updated.liked_genres[0] == "Noir"
    assert "Thriller" in updated.liked_genres


async def test_update_profile_runtime_percentile(db: sqlite3.Connection) -> None:
    for i, rt in enumerate([60, 90, 95, 100, 110, 120, 140]):
        q_candidates.upsert_candidate(
            db, _candidate(f"m{i}", f"Film {i}", runtime=rt)
        )
    events = [
        TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.MOVIE,
            archive_id=f"m{i}",
            kind=TasteEventKind.FINISHED,
            strength=0.9,
        )
        for i in range(7)
    ]
    provider = TFIDFProvider(conn=db)

    updated = await provider.update_profile(
        TasteProfile(version=0, updated_at=_NOW), events
    )

    # 95th percentile of [60,90,95,100,110,120,140] — index 95*7//100=6 → 140.
    assert updated.runtime_tolerance_minutes == 140


async def test_update_profile_rated_down_forces_disliked(db: sqlite3.Connection) -> None:
    provider = TFIDFProvider(conn=db)
    current = TasteProfile(
        version=0, updated_at=_NOW, liked_show_ids=["showX"]
    )
    events = [
        TasteEvent(
            timestamp=_NOW,
            content_type=ContentType.SHOW,
            show_id="showX",
            kind=TasteEventKind.RATED_DOWN,
            strength=0.9,
            source="roku_api",
        )
    ]

    updated = await provider.update_profile(current, events)

    assert "showX" in updated.disliked_show_ids
    assert "showX" not in updated.liked_show_ids


# --- parse_search -----------------------------------------------------------


async def test_parse_search_detects_decade() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("40s noir")

    assert flt.era == (1940, 1949)
    assert "noir" in flt.keywords
    # Decade token was stripped from keywords.
    assert "40s" not in flt.keywords


async def test_parse_search_detects_movie_content_type() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("find me a noir film")

    assert flt.content_types == [ContentType.MOVIE]
    assert "noir" in flt.keywords


async def test_parse_search_detects_show() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("mystery tv series from the 80s")

    assert flt.content_types == [ContentType.SHOW]
    assert flt.era == (1980, 1989)
    assert "mystery" in flt.keywords


async def test_parse_search_short_runtime() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("short documentary")

    assert flt.max_runtime_minutes == 40
    assert "documentary" in flt.keywords


async def test_parse_search_empty_query_is_safe() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("")

    assert flt.keywords == []
    assert flt.content_types is None
    assert flt.era is None


async def test_parse_search_era_range() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("films from 1940-1959")

    assert flt.era == (1940, 1959)


# --- llm_calls logging -----------------------------------------------------


async def test_rank_logs_llm_call_row(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(
        db, _candidate("m1", "Film 1", genres=["Noir"])
    )
    provider = TFIDFProvider(conn=db)

    await provider.rank(_profile(liked_genres=["Noir"]), [_candidate("m1", "Film 1")], n=1)

    row = db.execute(
        "SELECT provider, workflow FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["provider"] == "tfidf"
    assert row["workflow"] == "rank"


async def test_health_degraded_on_empty_corpus(db: sqlite3.Connection) -> None:
    provider = TFIDFProvider(conn=db)
    status = await provider.health_check()
    assert status.status == "degraded"


async def test_health_ok_with_rows(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1", "Film 1"))
    provider = TFIDFProvider(conn=db)
    status = await provider.health_check()
    assert status.status == "ok"


async def test_parse_search_passed_dates_since_are_not_keywords() -> None:
    provider = TFIDFProvider(conn=None)
    flt = await provider.parse_search("1940-1959 crime drama")
    # Year tokens (pure digits) filtered out from keywords.
    assert "crime" in flt.keywords
    assert "drama" in flt.keywords
    assert "1940" not in flt.keywords
    assert "1959" not in flt.keywords


# Silence unused import warnings from flake-like linters.
_ = timedelta
