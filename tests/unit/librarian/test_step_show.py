"""step_show executes the decision: downloads, places, updates state."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from archive_agent.archive.downloader import DownloadRequest, DownloadResult
from archive_agent.config import Config
from archive_agent.librarian.tv_sampler import step_show
from archive_agent.librarian.zones import Zone, zone_path
from archive_agent.state.models import Candidate, CandidateStatus, ContentType, ShowState
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import show_state as q_show_state

_SHOW_ID = "1433"
_SHOW_TITLE = "The Dick Van Dyke Show"
_NOW = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _episode(
    season: int,
    episode: int,
    *,
    status: CandidateStatus = CandidateStatus.NEW,
) -> Candidate:
    return Candidate(
        archive_id=f"ep-s{season:02d}e{episode:02d}",
        content_type=ContentType.EPISODE,
        title=f"Episode {season}x{episode}",
        show_id=_SHOW_ID,
        season=season,
        episode=episode,
        source_collection="television",
        status=status,
        discovered_at=_NOW - timedelta(days=20),
    )


def _make_fake_downloader(staged_root: Path):  # type: ignore[no-untyped-def]
    """Return a downloader callable that writes a fake file and
    reports DownloadResult(status=done). Tracks every invocation."""
    calls: list[DownloadRequest] = []

    async def _fake(req: DownloadRequest, conn: sqlite3.Connection) -> DownloadResult:
        calls.append(req)
        item_dir = staged_root / req.archive_id
        item_dir.mkdir(parents=True, exist_ok=True)
        video = item_dir / f"{req.archive_id}.mp4"
        video.write_bytes(b"\x00" * 500)
        return DownloadResult(
            archive_id=req.archive_id,
            status="done",
            file_path=video,
            size_bytes=500,
            format="h.264",
        )

    return _fake, calls


async def test_start_sampling_downloads_and_places(
    db: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch
) -> None:
    # Override staging root so the fake downloader writes under tmp_path
    monkeypatch.setattr(
        "archive_agent.librarian.tv_sampler._staging_root",
        lambda cfg: tmp_path / "staging",
    )
    for ep in (_episode(1, 1), _episode(1, 2), _episode(1, 3), _episode(1, 4)):
        q_candidates.upsert_candidate(db, ep)

    fake_dl, calls = _make_fake_downloader(tmp_path / "staging")
    result = await step_show(db, config, _SHOW_ID, fake_dl, show_title=_SHOW_TITLE, now=_NOW)

    assert result.action == "start_sampling"
    assert result.episodes_attempted == config.librarian.tv.sampler_episode_count == 3
    assert result.episodes_placed == 3
    assert result.errors == []
    # 3 download calls, all into TV_SAMPLER zone
    assert [c.zone for c in calls] == [Zone.TV_SAMPLER] * 3
    # Files landed in /media/tv-sampler/<show>/Season 01/
    sampler_root = zone_path(Zone.TV_SAMPLER, config) / _SHOW_TITLE / "Season 01"
    assert sampler_root.exists()
    assert len(list(sampler_root.glob("*.mp4"))) == 3
    # show_state row got seeded with started_at
    state = q_show_state.get(db, _SHOW_ID)
    assert state is not None
    assert state.started_at == _NOW
    # Candidate statuses flipped to SAMPLING
    after = {c.archive_id: c.status for c in q_candidates.list_by_show(db, _SHOW_ID)}
    assert after["ep-s01e01"] == CandidateStatus.SAMPLING
    assert after["ep-s01e02"] == CandidateStatus.SAMPLING
    assert after["ep-s01e03"] == CandidateStatus.SAMPLING
    # ep-s01e04 wasn't in the sampler set — still NEW
    assert after["ep-s01e04"] == CandidateStatus.NEW


async def test_promote_moves_sampler_and_queues_remainder(
    db: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "archive_agent.librarian.tv_sampler._staging_root",
        lambda cfg: tmp_path / "staging",
    )
    # Seed a full sampler state + engagement
    for ep in (
        _episode(1, 1, status=CandidateStatus.SAMPLING),
        _episode(1, 2, status=CandidateStatus.SAMPLING),
        _episode(1, 3, status=CandidateStatus.SAMPLING),
        _episode(1, 4),
        _episode(1, 5),
    ):
        q_candidates.upsert_candidate(db, ep)
    q_show_state.upsert(
        db,
        ShowState(
            show_id=_SHOW_ID,
            episodes_finished=2,
            episodes_abandoned=0,
            episodes_available=3,
            started_at=_NOW - timedelta(days=5),
            last_playback_at=_NOW - timedelta(days=1),
        ),
    )
    # Pre-create the sampler folder so promote_show has something to move
    sampler_dir = zone_path(Zone.TV_SAMPLER, config) / _SHOW_TITLE / "Season 01"
    sampler_dir.mkdir(parents=True)
    (sampler_dir / "ep1.mp4").write_bytes(b"\x00" * 500)
    (sampler_dir / "ep2.mp4").write_bytes(b"\x00" * 500)
    (sampler_dir / "ep3.mp4").write_bytes(b"\x00" * 500)

    fake_dl, calls = _make_fake_downloader(tmp_path / "staging")
    result = await step_show(db, config, _SHOW_ID, fake_dl, show_title=_SHOW_TITLE, now=_NOW)

    assert result.action == "promote"
    assert result.promoted is True
    # Two remaining S1 episodes queued into TV zone (not sampler)
    assert result.episodes_attempted == 2
    assert [c.zone for c in calls] == [Zone.TV, Zone.TV]
    assert result.episodes_placed == 2
    # Sampler folder gone; tv folder exists with merged contents
    assert not sampler_dir.exists()
    tv_dir = zone_path(Zone.TV, config) / _SHOW_TITLE
    assert tv_dir.exists()
    # Episodes 4 and 5 also landed under /media/tv/<show>/Season 01/
    s1_dir = tv_dir / "Season 01"
    assert s1_dir.exists()


async def test_wait_action_does_nothing(
    db: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch
) -> None:
    """wait = no-op: no downloads, no state mutation."""
    monkeypatch.setattr(
        "archive_agent.librarian.tv_sampler._staging_root",
        lambda cfg: tmp_path / "staging",
    )
    # Sampler partial (1/3) — decide_for_show returns wait
    q_candidates.upsert_candidate(db, _episode(1, 1, status=CandidateStatus.SAMPLING))
    q_show_state.upsert(
        db,
        ShowState(
            show_id=_SHOW_ID,
            episodes_finished=0,
            episodes_abandoned=0,
            episodes_available=1,
            started_at=_NOW - timedelta(days=2),
        ),
    )

    fake_dl, calls = _make_fake_downloader(tmp_path / "staging")
    result = await step_show(db, config, _SHOW_ID, fake_dl, show_title=_SHOW_TITLE, now=_NOW)

    assert result.action == "wait"
    assert result.episodes_placed == 0
    assert calls == []


async def test_evict_action_does_nothing(
    db: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch
) -> None:
    """evict = no-op too; phase2-07 handles the actual TTL sweep."""
    monkeypatch.setattr(
        "archive_agent.librarian.tv_sampler._staging_root",
        lambda cfg: tmp_path / "staging",
    )
    for n in (1, 2, 3):
        q_candidates.upsert_candidate(db, _episode(1, n, status=CandidateStatus.SAMPLING))
    q_show_state.upsert(
        db,
        ShowState(
            show_id=_SHOW_ID,
            episodes_finished=0,
            episodes_abandoned=0,
            episodes_available=3,
            started_at=_NOW - timedelta(days=40),  # past 14d window + some
        ),
    )

    fake_dl, calls = _make_fake_downloader(tmp_path / "staging")
    result = await step_show(db, config, _SHOW_ID, fake_dl, show_title=_SHOW_TITLE, now=_NOW)
    assert result.action == "evict"
    assert calls == []


async def test_download_failure_captured_in_errors(
    db: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "archive_agent.librarian.tv_sampler._staging_root",
        lambda cfg: tmp_path / "staging",
    )
    for ep in (_episode(1, 1), _episode(1, 2), _episode(1, 3)):
        q_candidates.upsert_candidate(db, ep)

    async def _failing_dl(req: DownloadRequest, conn: sqlite3.Connection) -> DownloadResult:
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error="mocked download failure",
        )

    result = await step_show(db, config, _SHOW_ID, _failing_dl, show_title=_SHOW_TITLE, now=_NOW)
    assert result.action == "start_sampling"
    assert result.episodes_attempted == 3
    assert result.episodes_placed == 0
    assert len(result.errors) == 3
    assert all("mocked download failure" in e for e in result.errors)
    # Even on failures, show_state was seeded (so next run sees we
    # started — avoids re-running start_sampling over and over when
    # downloads are flaky)
    state = q_show_state.get(db, _SHOW_ID)
    assert state is not None
