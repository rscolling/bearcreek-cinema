"""Feature rendering is deterministic and captures the signal we care
about: title, decade, type, genres, runtime bucket."""

from __future__ import annotations

from datetime import UTC, datetime

from archive_agent.ranking.tfidf.features import candidate_document, profile_document
from archive_agent.state.models import Candidate, ContentType, EraPreference, TasteProfile


def _movie(**overrides: object) -> Candidate:
    defaults: dict[str, object] = {
        "archive_id": "m1",
        "content_type": ContentType.MOVIE,
        "title": "His Girl Friday",
        "year": 1940,
        "runtime_minutes": 92,
        "genres": ["Comedy", "Romance"],
        "description": "Screwball newsroom romance.",
        "source_collection": "moviesandfilms",
        "discovered_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def test_candidate_document_is_deterministic() -> None:
    c = _movie()
    assert candidate_document(c) == candidate_document(c)


def test_candidate_document_contains_core_features() -> None:
    doc = candidate_document(_movie())
    assert "His Girl Friday" in doc
    assert "type_movie" in doc
    assert "decade_1940s" in doc
    assert "genre_comedy" in doc
    assert "genre_romance" in doc
    # 92 minutes -> feature
    assert "runtime_feature" in doc


def test_runtime_buckets() -> None:
    assert "runtime_short" in candidate_document(_movie(runtime_minutes=25))
    assert "runtime_medium" in candidate_document(_movie(runtime_minutes=70))
    assert "runtime_feature" in candidate_document(_movie(runtime_minutes=120))
    assert "runtime_long" in candidate_document(_movie(runtime_minutes=200))
    assert "runtime_unknown" in candidate_document(_movie(runtime_minutes=None))


def test_unknown_year_decade_token() -> None:
    doc = candidate_document(_movie(year=None))
    assert "decade_unknown" in doc


def test_multiword_genre_tokenization() -> None:
    doc = candidate_document(_movie(genres=["Film Noir"]))
    assert "genre_film_noir" in doc


def test_profile_document_doubles_liked_genres() -> None:
    profile = TasteProfile(
        version=1,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        liked_genres=["Noir"],
    )
    doc = profile_document(profile)
    # Doubled so TF weighting dominates
    assert doc.count("genre_noir") == 2


def test_profile_document_era_preferences() -> None:
    profile = TasteProfile(
        version=1,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        era_preferences=[
            EraPreference(decade=1940, weight=0.8),
            EraPreference(decade=2000, weight=-0.5),
        ],
    )
    doc = profile_document(profile)
    assert "decade_1940s" in doc
    assert "decade_2000s" not in doc  # negative weight excluded


def test_empty_profile_returns_nonempty_doc() -> None:
    """Empty profile must still produce something the vectorizer can
    transform — otherwise prefilter blows up on zero-length query."""
    profile = TasteProfile(version=0, updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert profile_document(profile).strip() != ""
