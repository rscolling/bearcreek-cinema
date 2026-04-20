"""End-to-end recommend pipeline — no real LLM, no real prefilter tuning.

The prefilter is real (it's just scikit-learn) but the provider is
stubbed, so tests assert on wiring (exclude window, profile version,
audit inserts, empty-pool handling) rather than model quality.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from archive_agent.commands.recommend import NoProfileError, recommend
from archive_agent.config import Config
from archive_agent.ranking.provider import HealthStatus
from archive_agent.state.models import (
    Candidate,
    ContentType,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import ranked_candidates as q_ranked
from archive_agent.state.queries import taste_profile_versions as q_profiles

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


# --- fixtures / helpers ----------------------------------------------------


def _candidate(archive_id: str, title: str, **overrides: Any) -> Candidate:
    defaults: dict[str, Any] = {
        "archive_id": archive_id,
        "content_type": ContentType.MOVIE,
        "title": title,
        "year": 1950,
        "runtime_minutes": 95,
        "genres": ["Noir"],
        "source_collection": "moviesandfilms",
        "discovered_at": _NOW,
    }
    defaults.update(overrides)
    return Candidate.model_validate(defaults)


def _seed(db: sqlite3.Connection, n: int = 8) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        aid = f"m{i:02d}"
        ids.append(aid)
        q_candidates.upsert_candidate(db, _candidate(aid, f"Film {i}", genres=["Noir"]))
    return ids


def _insert_profile(db: sqlite3.Connection) -> TasteProfile:
    profile = TasteProfile(
        version=0,
        updated_at=_NOW,
        summary="likes noir",
        liked_genres=["Noir"],
    )
    q_profiles.insert_profile(db, profile)
    got = q_profiles.get_latest_profile(db)
    assert got is not None
    return got


def _canned_picks(cands: list[Candidate], n: int) -> list[RankedCandidate]:
    picks = []
    for i, c in enumerate(cands[:n], start=1):
        picks.append(
            RankedCandidate(
                candidate=c,
                score=max(0.1, 1.0 - (i - 1) * 0.1),
                reasoning="canned reasoning from stub provider",
                rank=i,
            )
        )
    return picks


class _FakeProvider:
    def __init__(self, name: str = "ollama") -> None:
        self.name = name
        self.captured_ratings: dict[str, TasteEvent] | None = None
        self.captured_candidates: list[Candidate] | None = None

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        self.captured_ratings = ratings
        self.captured_candidates = candidates
        return _canned_picks(candidates, n)

    async def update_profile(self, current: TasteProfile, events: list[TasteEvent]) -> TasteProfile:
        return current

    async def parse_search(self, query: str) -> SearchFilter:
        return SearchFilter()


def _patch_provider(monkeypatch: pytest.MonkeyPatch, fake: _FakeProvider) -> None:
    """Route both factory entry points to the fake."""
    import archive_agent.commands.recommend as mod

    monkeypatch.setattr(mod, "make_provider", lambda *a, **k: fake)
    monkeypatch.setattr(mod, "make_provider_for_workflow", lambda *a, **k: fake)


# --- tests -----------------------------------------------------------------


async def test_happy_path_returns_picks_and_inserts_batch(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, n=5)
    _insert_profile(db)
    _patch_provider(monkeypatch, _FakeProvider())

    result = await recommend(db, config, n=3, now=_NOW)

    assert result.n_returned == 3
    assert len(result.items) == 3
    assert result.provider == "ollama"
    assert result.profile_version == 1
    assert result.batch_id
    # Batch landed in the audit table.
    row = db.execute(
        "SELECT COUNT(*) AS c FROM ranked_candidates WHERE batch_id = ?",
        (result.batch_id,),
    ).fetchone()
    assert row["c"] == 3


async def test_no_profile_raises(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, n=3)
    _patch_provider(monkeypatch, _FakeProvider())
    with pytest.raises(NoProfileError):
        await recommend(db, config, n=3, now=_NOW)


async def test_empty_candidate_pool_returns_no_items(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert_profile(db)  # profile exists, but no candidates
    _patch_provider(monkeypatch, _FakeProvider())

    result = await recommend(db, config, n=5, now=_NOW)

    assert result.n_returned == 0
    assert result.items == []
    assert result.batch_id == ""


async def test_exclude_window_skips_recent_archive_ids(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(db, n=6)
    _insert_profile(db)
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)

    # First run picks some; next run should exclude those.
    first = await recommend(db, config, n=3, now=_NOW)
    recommended_first = {r.candidate.archive_id for r in first.items}
    assert recommended_first

    second = await recommend(db, config, n=3, now=_NOW + timedelta(minutes=1))

    recommended_second = {r.candidate.archive_id for r in second.items}
    assert recommended_first.isdisjoint(recommended_second)
    assert second.excluded_count == len(recommended_first)
    # Every returned id is still in the universe we seeded.
    assert recommended_second.issubset(set(ids))


async def test_dry_run_skips_audit_insert(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, n=5)
    _insert_profile(db)
    _patch_provider(monkeypatch, _FakeProvider())

    result = await recommend(db, config, n=3, dry_run=True, now=_NOW)

    assert result.items
    assert result.batch_id == ""
    row = db.execute("SELECT COUNT(*) AS c FROM ranked_candidates").fetchone()
    assert row["c"] == 0


async def test_force_provider_overrides_workflow_config(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, n=4)
    _insert_profile(db)
    # The workflow config says "ollama" but we force tfidf.
    fake = _FakeProvider(name="tfidf")
    _patch_provider(monkeypatch, fake)

    result = await recommend(db, config, n=3, force_provider="tfidf", now=_NOW)

    assert result.provider == "tfidf"


async def test_ratings_are_threaded_into_rank(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archive_agent.state.models import TasteEventKind
    from archive_agent.state.queries import taste_events as q_taste_events

    _seed(db, n=4)
    _insert_profile(db)
    # Add a rating that should get bulk-read and passed to rank()
    q_taste_events.insert_event(
        db,
        TasteEvent(
            timestamp=_NOW - timedelta(days=1),
            content_type=ContentType.SHOW,
            show_id="showX",
            kind=TasteEventKind.RATED_LOVE,
            strength=1.0,
            source="roku_api",
        ),
    )
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)

    await recommend(db, config, n=2, now=_NOW)

    assert fake.captured_ratings is not None
    assert "showX" in fake.captured_ratings


async def test_latest_batch_round_trip(
    config: Config, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, n=5)
    _insert_profile(db)
    _patch_provider(monkeypatch, _FakeProvider())

    result = await recommend(db, config, n=3, now=_NOW)

    latest = q_ranked.latest_batch(db)
    assert len(latest) == 3
    assert [r.candidate.archive_id for r in latest] == [
        r.candidate.archive_id for r in result.items
    ]
