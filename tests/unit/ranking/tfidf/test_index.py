"""Build + save/load round-trip + refresh semantics for TFIDFIndex."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from archive_agent.ranking.tfidf.index import (
    TFIDFIndex,
    TFIDFPickleError,
    load_or_build,
)
from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates


def _make_candidate(
    archive_id: str,
    title: str,
    *,
    year: int = 1950,
    genres: list[str] | None = None,
    content_type: ContentType = ContentType.MOVIE,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        runtime_minutes=95,
        genres=genres or ["Drama"],
        source_collection="moviesandfilms",
        discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _seed_db(conn: sqlite3.Connection, n: int = 10) -> None:
    for i in range(n):
        q_candidates.upsert_candidate(
            conn,
            _make_candidate(
                f"item_{i:03d}",
                f"Film Number {i}",
                year=1940 + (i % 5) * 10,
                genres=["Noir"] if i % 2 == 0 else ["Comedy"],
            ),
        )


def test_build_produces_matrix_aligned_with_archive_ids(db: sqlite3.Connection) -> None:
    _seed_db(db, n=12)
    index = TFIDFIndex.build(db)

    assert index.size == 12
    assert index.matrix.shape[0] == 12
    assert index.archive_ids == sorted(index.archive_ids)
    assert index.row_for("item_005") == 5


def test_empty_corpus_is_safe(db: sqlite3.Connection) -> None:
    index = TFIDFIndex.build(db)
    assert index.size == 0
    assert index.matrix.shape[0] == 0


def test_save_load_round_trip(db: sqlite3.Connection, tmp_path: Path) -> None:
    _seed_db(db, n=8)
    built = TFIDFIndex.build(db)
    path = tmp_path / "sub" / "index.pkl"  # exercises parent mkdir
    built.save(path)

    assert path.exists()

    loaded = TFIDFIndex.load(path)
    assert loaded.archive_ids == built.archive_ids
    assert loaded.matrix.shape == built.matrix.shape
    # Same vocabulary size == same vectorizer fit
    assert len(loaded.vectorizer.vocabulary_) == len(built.vectorizer.vocabulary_)


def test_refresh_picks_up_new_candidates(db: sqlite3.Connection) -> None:
    _seed_db(db, n=5)
    index = TFIDFIndex.build(db)
    assert index.size == 5

    q_candidates.upsert_candidate(db, _make_candidate("new_one", "Brand New Film"))
    index.refresh(db)

    assert index.size == 6
    assert "new_one" in index.archive_ids


def test_load_rejects_foreign_pickle(tmp_path: Path) -> None:
    import pickle

    path = tmp_path / "bad.pkl"
    path.write_bytes(pickle.dumps({"not": "ours"}))

    with pytest.raises(TFIDFPickleError):
        TFIDFIndex.load(path)


def test_load_or_build_falls_back_on_corrupt_pickle(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_db(db, n=4)
    path = tmp_path / "corrupt.pkl"
    path.write_bytes(b"not a pickle at all")

    index = load_or_build(db, path)
    assert index.size == 4


def test_load_or_build_uses_cache_when_present(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_db(db, n=3)
    built = TFIDFIndex.build(db)
    path = tmp_path / "cache.pkl"
    built.save(path)

    # Corrupt the DB (drop a candidate) — if load_or_build rebuilt,
    # the loaded archive_ids would diverge.
    db.execute("DELETE FROM candidates WHERE archive_id = 'item_001'")
    db.commit()

    index = load_or_build(db, path)
    assert "item_001" in index.archive_ids  # proved we loaded the cache
