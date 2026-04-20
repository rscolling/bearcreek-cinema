"""FTS5 catalog search — trigram typo tolerance, triggers, filters."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import search as q_search

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(
    archive_id: str,
    title: str,
    *,
    description: str = "",
    content_type: ContentType = ContentType.MOVIE,
    year: int = 1950,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=title,
        year=year,
        runtime_minutes=95,
        genres=["Drama"],
        description=description,
        source_collection="moviesandfilms" if content_type == ContentType.MOVIE else "television",
        discovered_at=_NOW,
    )


@pytest.fixture
def seeded(db: sqlite3.Connection) -> sqlite3.Connection:
    """A fresh DB populated with ~20 varied candidates."""
    seed = [
        _candidate("third_man_1949", "The Third Man", description="Postwar Vienna noir."),
        _candidate("his_girl_friday", "His Girl Friday", description="Screwball newsroom comedy."),
        _candidate("thin_man_1934", "The Thin Man", description="Witty mystery."),
        _candidate(
            "beverly_hillbillies_s01", "The Beverly Hillbillies",
            description="Sitcom about an Ozark family.",
            content_type=ContentType.SHOW,
        ),
        _candidate("dick_van_dyke_s01", "The Dick Van Dyke Show",
                   description="Early-1960s comedy.", content_type=ContentType.SHOW),
        _candidate("night_of_living_dead", "Night of the Living Dead",
                   description="Low-budget horror classic.", year=1968),
        _candidate("casablanca_1942", "Casablanca",
                   description="Wartime romance in Morocco.", year=1942),
        _candidate("nosferatu_1922", "Nosferatu",
                   description="German Expressionist vampire film.", year=1922),
        _candidate("metropolis_1927", "Metropolis",
                   description="Futurist silent film.", year=1927),
        _candidate("battleship_potemkin", "Battleship Potemkin",
                   description="Soviet montage-era classic.", year=1925),
        _candidate("plan_9", "Plan 9 from Outer Space",
                   description="Alien-invasion B-movie."),
        _candidate("detour_1945", "Detour", description="Poverty Row noir."),
        _candidate("trainland_1900", "The Great Train Robbery",
                   description="Early narrative short."),
        _candidate("dr_jekyll_1931", "Dr. Jekyll and Mr. Hyde",
                   description="Pre-Code horror."),
        _candidate("twilight_zone_s01", "The Twilight Zone",
                   description="Anthology sci-fi series.", content_type=ContentType.SHOW),
        _candidate("charade_1963", "Charade",
                   description="Romantic thriller in Paris."),
    ]
    for c in seed:
        q_candidates.upsert_candidate(db, c)
    return db


def test_exact_match_returns_expected_best(seeded: sqlite3.Connection) -> None:
    results = q_search.fts_search(seeded, "third man", limit=5)

    assert results
    assert results[0][0].archive_id == "third_man_1949"
    # Score in our normalized range.
    assert 0.0 < results[0][1] <= 1.0


def test_partial_prefix_matches_via_trigram(seeded: sqlite3.Connection) -> None:
    """Trigram FTS5 gives substring tolerance — a prefix like 'thir'
    still finds 'The Third Man'. Transposition (e.g., 'thrid') does NOT
    match because no trigram of 'thrid' appears in 'third'; the test
    that mattered historically is substring-style tolerance for ASR
    drops and partial typing."""
    results = q_search.fts_search(seeded, "thir", limit=5)
    ids = [c.archive_id for c, _ in results]
    assert "third_man_1949" in ids


def test_substring_match_tolerates_trailing_chars(seeded: sqlite3.Connection) -> None:
    results = q_search.fts_search(seeded, "hillbi", limit=5)
    ids = [c.archive_id for c, _ in results]
    assert "beverly_hillbillies_s01" in ids


def test_description_hit_also_surfaces(seeded: sqlite3.Connection) -> None:
    # "Expressionist" appears only in the description of Nosferatu.
    results = q_search.fts_search(seeded, "Expressionist", limit=5)
    ids = [c.archive_id for c, _ in results]
    assert "nosferatu_1922" in ids


def test_content_type_filter(seeded: sqlite3.Connection) -> None:
    results = q_search.fts_search(
        seeded, "comedy", limit=10, content_type=ContentType.SHOW
    )
    assert results
    assert all(c.content_type == ContentType.SHOW for c, _ in results)


def test_empty_query_returns_empty(seeded: sqlite3.Connection) -> None:
    assert q_search.fts_search(seeded, "", limit=10) == []
    assert q_search.fts_search(seeded, "   ", limit=10) == []


def test_no_match_returns_empty(seeded: sqlite3.Connection) -> None:
    assert q_search.fts_search(seeded, "zzqqxx_not_a_word", limit=5) == []


def test_limit_is_respected(seeded: sqlite3.Connection) -> None:
    results = q_search.fts_search(seeded, "man", limit=1)
    assert len(results) <= 1


def test_scores_descend(seeded: sqlite3.Connection) -> None:
    results = q_search.fts_search(seeded, "man", limit=5)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


# --- autocomplete ---------------------------------------------------------


def test_autocomplete_prefix_matches(seeded: sqlite3.Connection) -> None:
    suggestions = q_search.fts_autocomplete(seeded, "the t", limit=5)
    titles = {s["title"] for s in suggestions}
    # Both "The Third Man" and "The Thin Man" and "The Twilight Zone" qualify.
    assert titles & {"The Third Man", "The Thin Man", "The Twilight Zone"}


def test_autocomplete_empty_returns_empty(seeded: sqlite3.Connection) -> None:
    assert q_search.fts_autocomplete(seeded, "", limit=5) == []


def test_autocomplete_limit(seeded: sqlite3.Connection) -> None:
    suggestions = q_search.fts_autocomplete(seeded, "the", limit=2)
    assert len(suggestions) <= 2


# --- triggers keep FTS in sync -------------------------------------------


def test_insert_propagates_to_fts(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(
        db, _candidate("new_one", "Brand New Title", description="Fresh description.")
    )
    results = q_search.fts_search(db, "Brand New", limit=5)
    assert [c.archive_id for c, _ in results] == ["new_one"]


def test_update_propagates_to_fts(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("x1", "Original Title"))
    # Overwrite via upsert (UPDATE path).
    q_candidates.upsert_candidate(db, _candidate("x1", "Updated Title"))

    stale = q_search.fts_search(db, "Original", limit=5)
    fresh = q_search.fts_search(db, "Updated", limit=5)

    assert stale == []
    assert fresh and fresh[0][0].archive_id == "x1"


def test_update_multi_column_fires_once(db: sqlite3.Connection) -> None:
    """Per the ADR note: title + description changing in one UPDATE
    must not double-insert into the FTS table."""
    q_candidates.upsert_candidate(
        db, _candidate("x2", "Initial", description="Initial desc")
    )
    q_candidates.upsert_candidate(
        db, _candidate("x2", "Second", description="Second desc")
    )

    count = db.execute(
        "SELECT COUNT(*) AS c FROM candidates_fts WHERE archive_id = ?",
        ("x2",),
    ).fetchone()["c"]
    assert count == 1


def test_delete_propagates_to_fts(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _candidate("d1", "Goes Away"))
    assert q_search.fts_search(db, "Goes Away", limit=5)

    db.execute("DELETE FROM candidates WHERE archive_id = 'd1'")
    db.commit()

    assert q_search.fts_search(db, "Goes Away", limit=5) == []
