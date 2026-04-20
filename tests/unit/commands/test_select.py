"""``select_candidate`` + ``commit_show`` — orchestration, not networking.

Download, placement, Jellyfin scan, and the TV sampler are all
monkey-patched so the tests stay offline. We're asserting on the
orchestration: idempotence, failure propagation, status transitions.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from archive_agent.commands.select import (
    CandidateNotFoundError,
    commit_show,
    select_candidate,
)
from archive_agent.config import Config
from archive_agent.state.models import (
    Candidate,
    CandidateStatus,
    ContentType,
)
from archive_agent.state.queries import candidates as q_candidates

_NOW = datetime(2026, 4, 20, tzinfo=UTC)


def _candidate(
    archive_id: str,
    *,
    content_type: ContentType = ContentType.MOVIE,
    jf_item_id: str | None = None,
    show_id: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    size_bytes: int | None = None,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=content_type,
        title=f"Title {archive_id}",
        year=1950,
        runtime_minutes=95,
        genres=["Drama"],
        source_collection="moviesandfilms" if content_type == ContentType.MOVIE else "television",
        discovered_at=_NOW,
        jellyfin_item_id=jf_item_id,
        show_id=show_id,
        season=season,
        episode=episode,
        size_bytes=size_bytes,
    )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    download_status: str = "done",
    download_file: Path | None = None,
    resolved_item_id: str | None = "jf-abc",
    step_show_action: str = "start_sampling",
) -> dict[str, int]:
    """Replace downloader + place + scan + step_show with stubs.

    Returns a counter dict so tests can assert on how many times each
    seam was exercised.
    """
    import archive_agent.commands.select as mod
    from archive_agent.archive.downloader import DownloadResult
    from archive_agent.librarian.placement import PlaceResult
    from archive_agent.librarian.tv_sampler import SamplerResult
    from archive_agent.librarian.zones import Zone

    counts = {"download": 0, "place": 0, "scan": 0, "step_show": 0}

    async def _download(req: Any, conn: Any, **_: Any) -> DownloadResult:
        counts["download"] += 1
        return DownloadResult(
            archive_id=req.archive_id,
            status=download_status,  # type: ignore[arg-type]
            file_path=download_file or Path("/tmp/fake.mkv"),
            size_bytes=123,
            format="mkv",
        )

    def _place(
        conn: Any, config: Any, *, candidate: Candidate, source_path: Path, zone: Zone, **_: Any
    ) -> PlaceResult:
        counts["place"] += 1
        return PlaceResult(
            archive_id=candidate.archive_id,
            zone=zone,
            source_path=source_path,
            dest_path=source_path,
            moved=True,
            size_bytes=0,
        )

    async def _resolve(config: Any, archive_id: str, zone: Any, conn: Any) -> str | None:
        counts["scan"] += 1
        return resolved_item_id

    async def _step_show(
        conn: Any, config: Any, show_id: str, downloader: Any, **_: Any
    ) -> SamplerResult:
        counts["step_show"] += 1
        return SamplerResult(
            show_id=show_id,
            action=step_show_action,  # type: ignore[arg-type]
            reason="stubbed",
            episodes_attempted=3,
        )

    monkeypatch.setattr(mod, "download_one", _download)
    monkeypatch.setattr(mod, "place", _place)
    monkeypatch.setattr(mod, "_resolve_after_placement", _resolve)
    monkeypatch.setattr(mod, "step_show", _step_show)
    return counts


# --- select: movie ---------------------------------------------------------


async def test_select_movie_idempotent_when_already_placed(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1", jf_item_id="jf-existing"))
    counts = _patch_pipeline(monkeypatch)

    result = await select_candidate(db, config, "m1")

    assert result.status == "ready"
    assert result.jellyfin_item_id == "jf-existing"
    # No downloader / scanner called — idempotent fast path.
    assert counts == {"download": 0, "place": 0, "scan": 0, "step_show": 0}


async def test_select_movie_downloads_places_and_resolves(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    counts = _patch_pipeline(monkeypatch, resolved_item_id="jf-new")

    result = await select_candidate(db, config, "m1")

    assert result.status == "ready"
    assert result.jellyfin_item_id == "jf-new"
    assert counts["download"] == 1
    assert counts["place"] == 1
    assert counts["scan"] == 1
    # Candidate moved to DOWNLOADED.
    cand = q_candidates.get_by_archive_id(db, "m1")
    assert cand is not None
    assert cand.status == CandidateStatus.DOWNLOADED


async def test_select_movie_download_failure_returns_failed(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    _patch_pipeline(monkeypatch, download_status="failed")

    result = await select_candidate(db, config, "m1")

    assert result.status == "failed"
    assert "download failed" in result.detail


async def test_select_movie_scan_timeout_returns_queued(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    q_candidates.upsert_candidate(db, _candidate("m1"))
    _patch_pipeline(monkeypatch, resolved_item_id=None)

    result = await select_candidate(db, config, "m1")

    assert result.status == "queued"
    assert "awaiting Jellyfin scan" in result.detail


async def test_select_unknown_candidate_raises(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch)
    with pytest.raises(CandidateNotFoundError):
        await select_candidate(db, config, "does-not-exist")


# --- select: show ----------------------------------------------------------


async def test_select_show_with_placed_episode_returns_ready(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Show candidate + an episode that already has a jf item id.
    q_candidates.upsert_candidate(
        db, _candidate("show-card", content_type=ContentType.SHOW, show_id="showA")
    )
    q_candidates.upsert_candidate(
        db,
        _candidate(
            "show-ep1",
            content_type=ContentType.EPISODE,
            show_id="showA",
            season=1,
            episode=1,
            jf_item_id="jf-ep1",
        ),
    )
    counts = _patch_pipeline(monkeypatch)

    result = await select_candidate(db, config, "show-card")

    assert result.status == "ready"
    assert result.jellyfin_item_id == "jf-ep1"
    assert result.next_episode is not None
    assert result.next_episode.season == 1
    assert result.next_episode.episode == 1
    assert counts["step_show"] == 1


async def test_select_show_without_placed_episode_returns_queued(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    q_candidates.upsert_candidate(
        db, _candidate("show-card", content_type=ContentType.SHOW, show_id="showB")
    )
    # No episodes yet in DB for showB.
    _patch_pipeline(monkeypatch, step_show_action="start_sampling")

    result = await select_candidate(db, config, "show-card")

    assert result.status == "queued"
    assert result.detail == "sampler_start_sampling"


# --- commit_show -----------------------------------------------------------


async def test_commit_show_enqueues_episodes_and_estimates_size(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(1, 4):
        q_candidates.upsert_candidate(
            db,
            _candidate(
                f"ep{i}",
                content_type=ContentType.EPISODE,
                show_id="showC",
                season=1,
                episode=i,
                size_bytes=2_000_000_000,
            ),
        )
    _patch_pipeline(monkeypatch)

    result = await commit_show(db, config, "showC")

    assert result.enqueued_downloads == 3
    assert result.estimated_gb == pytest.approx(6.0)


async def test_commit_show_with_no_episodes_returns_zero(
    db: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch)
    result = await commit_show(db, config, "nothing")
    assert result.enqueued_downloads == 0
    assert result.estimated_gb == 0.0
