"""CRUD + window semantics for the ranked_candidates audit log."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from archive_agent.state.models import Candidate, ContentType, RankedCandidate
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import ranked_candidates as q_ranked

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(archive_id: str) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.MOVIE,
        title=f"Title {archive_id}",
        year=1950,
        genres=["Drama"],
        source_collection="moviesandfilms",
        discovered_at=_NOW,
    )


def _pick(archive_id: str, rank: int, score: float = 0.9) -> RankedCandidate:
    return RankedCandidate(
        candidate=_candidate(archive_id),
        score=score,
        reasoning="r" * 20,
        rank=rank,
    )


def test_insert_and_latest_batch(db: sqlite3.Connection) -> None:
    for aid in ("a", "b", "c"):
        q_candidates.upsert_candidate(db, _candidate(aid))
    items = [_pick("a", 1), _pick("b", 2), _pick("c", 3)]
    q_ranked.insert_batch(db, "batch1", items, provider="ollama", profile_version=1, now=_NOW)

    latest = q_ranked.latest_batch(db)

    assert [r.candidate.archive_id for r in latest] == ["a", "b", "c"]
    assert [r.rank for r in latest] == [1, 2, 3]


def test_latest_batch_empty_returns_empty(db: sqlite3.Connection) -> None:
    assert q_ranked.latest_batch(db) == []


def test_latest_batch_returns_newest(db: sqlite3.Connection) -> None:
    for aid in ("a", "b"):
        q_candidates.upsert_candidate(db, _candidate(aid))

    q_ranked.insert_batch(
        db,
        "older",
        [_pick("a", 1)],
        provider="ollama",
        profile_version=1,
        now=_NOW - timedelta(hours=2),
    )
    q_ranked.insert_batch(
        db,
        "newer",
        [_pick("b", 1)],
        provider="ollama",
        profile_version=1,
        now=_NOW,
    )

    latest = q_ranked.latest_batch(db)

    assert len(latest) == 1
    assert latest[0].candidate.archive_id == "b"


def test_recent_archive_ids_respects_window(db: sqlite3.Connection) -> None:
    for aid in ("old", "new"):
        q_candidates.upsert_candidate(db, _candidate(aid))

    q_ranked.insert_batch(
        db,
        "old_batch",
        [_pick("old", 1)],
        provider="ollama",
        profile_version=1,
        now=_NOW - timedelta(days=30),
    )
    q_ranked.insert_batch(
        db,
        "new_batch",
        [_pick("new", 1)],
        provider="ollama",
        profile_version=1,
        now=_NOW - timedelta(days=3),
    )

    recent = q_ranked.recent_archive_ids(db, since=_NOW - timedelta(days=14))

    assert recent == {"new"}


def test_latest_batch_drops_entries_with_missing_candidate(
    db: sqlite3.Connection,
) -> None:
    q_candidates.upsert_candidate(db, _candidate("still_here"))
    # Pretend "ghost" was in candidates when we inserted, then got deleted.
    q_candidates.upsert_candidate(db, _candidate("ghost"))
    q_ranked.insert_batch(
        db,
        "b1",
        [_pick("still_here", 1), _pick("ghost", 2)],
        provider="ollama",
        profile_version=1,
        now=_NOW,
    )
    db.execute("DELETE FROM candidates WHERE archive_id = 'ghost'")
    db.commit()

    latest = q_ranked.latest_batch(db)

    assert len(latest) == 1
    assert latest[0].candidate.archive_id == "still_here"
