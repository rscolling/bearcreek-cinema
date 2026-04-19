"""Downloader unit tests — no network, no subprocess.

Covers:

- ``pick_format`` choice logic against fixture file lists
- ``_select_backend`` resolves ia-get when on PATH, falls back otherwise
- ``download_one`` happy path with a mocked library backend
- ``download_one`` already-done short-circuit (``status=skipped``)
- Failed-row retry: a previous ``failed`` row doesn't block a new attempt
- ``dry_run`` writes no rows and touches no filesystem
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from archive_agent.archive import downloader
from archive_agent.archive.downloader import (
    DownloadRequest,
    DownloadResult,
    _select_backend,
    download_one,
    pick_format,
)
from archive_agent.librarian.zones import Zone
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


# --- pick_format ----------------------------------------------------------


def _file(name: str, fmt: str, source: str = "original") -> dict[str, Any]:
    return {"name": name, "format": fmt, "source": source}


def test_pick_format_prefers_h264_over_mpeg4() -> None:
    files = [
        _file("movie.mpeg", "MPEG4"),
        _file("movie.mp4", "h.264"),
        _file("movie.ogv", "Ogg Video"),
    ]
    chosen = pick_format(files, ["h.264", "mpeg4", "ogg video"])
    assert chosen is not None
    assert chosen["name"] == "movie.mp4"


def test_pick_format_falls_through_preferences() -> None:
    files = [_file("movie.mkv", "Matroska")]
    chosen = pick_format(files, ["h.264", "matroska"])
    assert chosen is not None
    assert chosen["name"] == "movie.mkv"


def test_pick_format_returns_none_when_no_video() -> None:
    files = [
        _file("thumb.jpg", "JPEG Thumb"),
        _file("metadata.xml", "Metadata"),
        {"name": "cover.png", "format": "PNG"},
    ]
    assert pick_format(files, ["h.264"]) is None


def test_pick_format_ignores_derivative_when_original_exists() -> None:
    """Archive.org often re-encodes; prefer the uploader's original."""
    files = [
        _file("movie_derivative.mp4", "h.264", source="derivative"),
        _file("movie.mpeg", "MPEG4", source="original"),
    ]
    chosen = pick_format(files, ["h.264", "mpeg4"])
    # Originals pool only has MPEG4; derivative MP4 is excluded even
    # though h.264 is higher preference.
    assert chosen is not None
    assert chosen["name"] == "movie.mpeg"


def test_pick_format_falls_back_to_all_videos_if_no_originals() -> None:
    files = [_file("only.mp4", "h.264", source="derivative")]
    chosen = pick_format(files, ["h.264"])
    assert chosen is not None
    assert chosen["name"] == "only.mp4"


def test_pick_format_recognizes_video_by_extension() -> None:
    """Some Archive.org items have opaque 'format' strings — fall back
    to file extension detection."""
    files = [{"name": "film.mp4", "format": "Custom-Video-Container", "source": "original"}]
    chosen = pick_format(files, ["h.264"])
    assert chosen is not None
    assert chosen["name"] == "film.mp4"


# --- backend selection ---------------------------------------------------


def test_select_backend_prefers_ia_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ia-get")
    assert _select_backend() == "ia_get"


def test_select_backend_falls_back_to_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda name: None)
    assert _select_backend() == "library"


# --- download_one lifecycle ----------------------------------------------


class _FakeBackend:
    """Records calls and delivers predetermined results."""

    def __init__(self, result: DownloadResult) -> None:
        self.result = result
        self.calls: int = 0

    async def __call__(
        self,
        req: DownloadRequest,
        conn: sqlite3.Connection,
        row_id: int,
    ) -> DownloadResult:
        self.calls += 1
        # Simulate a realistic status transition so the tests exercise
        # the library path's behavior.
        from archive_agent.state.queries import downloads as q

        q.update_progress(conn, row_id, status="downloading")
        if self.result.status == "done":
            q.update_progress(
                conn,
                row_id,
                status="done",
                path=str(self.result.file_path) if self.result.file_path else None,
                size_bytes=self.result.size_bytes,
            )
        elif self.result.status == "failed":
            q.update_progress(conn, row_id, status="failed", error=self.result.error or "mock")
        return self.result


def _install_fake_backend(monkeypatch: pytest.MonkeyPatch, fake: _FakeBackend) -> None:
    """Force the library backend and wire in our fake."""
    monkeypatch.setattr(downloader, "_select_backend", lambda: "library")
    monkeypatch.setattr(downloader, "_download_with_library", fake)


async def test_download_one_happy_path(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "sita" / "sita.mp4"
    fake = _FakeBackend(
        DownloadResult(
            archive_id="sita_sings",
            status="done",
            file_path=file_path,
            size_bytes=12345,
            format="h.264",
        )
    )
    _install_fake_backend(monkeypatch, fake)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path, zone=Zone.RECOMMENDATIONS)
    result = await download_one(req, db)
    assert result.status == "done"
    assert result.size_bytes == 12345
    assert fake.calls == 1
    # Row lifecycle
    row = db.execute(
        "SELECT status, path, size_bytes, started_at, finished_at FROM downloads"
    ).fetchone()
    assert row["status"] == "done"
    assert row["size_bytes"] == 12345
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


async def test_download_one_skips_when_already_done(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archive_agent.state.queries import downloads as q

    # Seed a done row
    row_id = q.insert(db, "sita_sings", "recommendations")
    q.update_progress(
        db,
        row_id,
        status="done",
        path=str(tmp_path / "sita.mp4"),
        size_bytes=999,
    )
    fake = _FakeBackend(DownloadResult(archive_id="sita_sings", status="done", size_bytes=0))
    _install_fake_backend(monkeypatch, fake)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path)
    result = await download_one(req, db)
    assert result.status == "skipped"
    assert result.size_bytes == 999
    assert fake.calls == 0  # backend never ran


async def test_download_one_retries_after_failure(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archive_agent.state.queries import downloads as q

    # Seed a failed row
    row_id = q.insert(db, "sita_sings", "recommendations")
    q.update_progress(db, row_id, status="failed", error="net hiccup")
    fake = _FakeBackend(
        DownloadResult(
            archive_id="sita_sings",
            status="done",
            file_path=tmp_path / "sita.mp4",
            size_bytes=1000,
        )
    )
    _install_fake_backend(monkeypatch, fake)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path)
    result = await download_one(req, db)
    assert result.status == "done"
    # Row count stayed at 1 — retries update in place
    total = db.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
    assert total == 1
    row = db.execute("SELECT status, error FROM downloads").fetchone()
    assert row["status"] == "done"
    # error from prior attempt still on the row (update_progress only
    # sets error when the status is failed)
    assert row["error"] == "net hiccup"


async def test_download_one_records_failure(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeBackend(
        DownloadResult(
            archive_id="sita_sings",
            status="failed",
            error="no suitable video file",
        )
    )
    _install_fake_backend(monkeypatch, fake)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path)
    result = await download_one(req, db)
    assert result.status == "failed"
    row = db.execute("SELECT status, error FROM downloads").fetchone()
    assert row["status"] == "failed"
    assert "no suitable" in row["error"]


async def test_download_one_dry_run_is_side_effect_free(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeBackend(DownloadResult(archive_id="sita_sings", status="done", size_bytes=0))
    _install_fake_backend(monkeypatch, fake)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path, dry_run=True)
    result = await download_one(req, db)
    assert result.status == "skipped"
    assert fake.calls == 0
    count = db.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
    assert count == 0
    assert not (tmp_path / "sita_sings").exists()


async def test_download_one_exception_records_failed_row(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(req, conn, row_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("network partitioned")

    monkeypatch.setattr(downloader, "_select_backend", lambda: "library")
    monkeypatch.setattr(downloader, "_download_with_library", _boom)
    req = DownloadRequest(archive_id="sita_sings", dest_dir=tmp_path)
    result = await download_one(req, db)
    assert result.status == "failed"
    assert "network partitioned" in (result.error or "")
    row = db.execute("SELECT status, error FROM downloads").fetchone()
    assert row["status"] == "failed"
    assert "RuntimeError" in row["error"]
