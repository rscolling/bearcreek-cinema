"""Cosine prefilter surfaces matches for liked genres and honors
disliked IDs / content-type filters."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from archive_agent.ranking.tfidf.index import TFIDFIndex
from archive_agent.ranking.tfidf.prefilter import prefilter
from archive_agent.state.models import Candidate, ContentType, TasteProfile
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _candidate(
    archive_id: str,
    title: str,
    *,
    genres: list[str],
    content_type: ContentType = ContentType.MOVIE,
    year: int = 1950,
    show_id: str | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        runtime_minutes=95,
        genres=genres,
        show_id=show_id,
        source_collection="moviesandfilms" if content_type == ContentType.MOVIE else "television",
        discovered_at=_NOW,
    )


def _profile(
    liked_genres: list[str] | None = None,
    *,
    disliked_archive_ids: list[str] | None = None,
    disliked_show_ids: list[str] | None = None,
) -> TasteProfile:
    return TasteProfile(
        version=1,
        updated_at=_NOW,
        liked_genres=liked_genres or [],
        disliked_archive_ids=disliked_archive_ids or [],
        disliked_show_ids=disliked_show_ids or [],
    )


def _seed_mixed(db: sqlite3.Connection) -> None:
    items = [
        _candidate("noir1", "Shadow Alley", genres=["Noir"]),
        _candidate("noir2", "Night Street", genres=["Noir"]),
        _candidate("com1", "Light Hearted", genres=["Comedy"]),
        _candidate("com2", "Funny Business", genres=["Comedy"]),
        _candidate("doc1", "Nature Notes", genres=["Documentary"]),
        _candidate(
            "show_ep1",
            "Detective Show S01E01",
            genres=["Noir"],
            content_type=ContentType.EPISODE,
            show_id="showA",
        ),
    ]
    for c in items:
        q_candidates.upsert_candidate(db, c)


def test_liked_genre_surfaces_matching_candidates(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(liked_genres=["Noir"]), k=10)

    top_ids = [c.archive_id for c, _ in picks[:3]]
    assert "noir1" in top_ids
    assert "noir2" in top_ids
    # Noir show episode shares the genre and should rank above documentaries.
    assert "show_ep1" in top_ids


def test_scores_are_in_unit_interval(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(liked_genres=["Noir"]), k=10)

    for _, score in picks:
        assert 0.0 <= score <= 1.0


def test_disliked_archive_ids_excluded(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(
        index,
        db,
        _profile(liked_genres=["Noir"], disliked_archive_ids=["noir1"]),
        k=10,
    )

    assert all(c.archive_id != "noir1" for c, _ in picks)


def test_disliked_show_ids_excluded(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(
        index,
        db,
        _profile(liked_genres=["Noir"], disliked_show_ids=["showA"]),
        k=10,
    )

    # The noir-tagged episode belongs to showA — must not appear.
    assert all(c.archive_id != "show_ep1" for c, _ in picks)


def test_content_type_filter_restricts_pool(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(
        index,
        db,
        _profile(liked_genres=["Noir"]),
        k=10,
        content_types=[ContentType.MOVIE],
    )

    assert picks
    assert all(c.content_type == ContentType.MOVIE for c, _ in picks)


def test_exclude_archive_ids(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(
        index,
        db,
        _profile(liked_genres=["Noir"]),
        k=10,
        exclude_archive_ids={"noir1", "noir2"},
    )

    ids = {c.archive_id for c, _ in picks}
    assert "noir1" not in ids
    assert "noir2" not in ids


def test_empty_profile_returns_empty_picks(db: sqlite3.Connection) -> None:
    """No liked signal at all -> cosine is 0 against every candidate."""
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(), k=10)
    # With zero signal every score is 0; prefilter drops zeros.
    assert picks == []


def test_empty_index_returns_empty(db: sqlite3.Connection) -> None:
    # No candidates in DB
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(liked_genres=["Noir"]), k=10)
    assert picks == []


def test_k_truncates(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(liked_genres=["Noir"]), k=2)
    assert len(picks) <= 2


def test_picks_are_sorted_descending_by_score(db: sqlite3.Connection) -> None:
    _seed_mixed(db)
    index = TFIDFIndex.build(db)
    picks = prefilter(index, db, _profile(liked_genres=["Noir", "Comedy"]), k=10)

    scores = [s for _, s in picks]
    assert scores == sorted(scores, reverse=True)
