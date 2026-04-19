"""Archive.org item downloader with resume + MP4 preference.

Backends:

- ``ia-get`` (Rust binary) if present on PATH — better resume semantics
  and checksum verification. Invoked as a subprocess.
- Python ``internetarchive`` library otherwise — always available since
  it's in ``pyproject.toml`` deps.

The downloader always writes to a **staging** directory, never directly
into a ``/media/*`` zone (see phase2-06 for placement). The
``downloads`` row records the *intended* zone so placement knows where
to move the file once it's complete. A row's ``status`` progresses
``queued → downloading → done | failed | aborted``; re-running on a
``done`` row short-circuits to ``skipped`` without re-downloading.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from archive_agent.librarian.zones import Zone
from archive_agent.logging import get_logger
from archive_agent.state.queries import downloads as q_downloads

__all__ = [
    "DownloadRequest",
    "DownloadResult",
    "_select_backend",
    "download_one",
    "pick_format",
]

log = get_logger("archive_agent.archive.downloader")

_DEFAULT_FORMATS: tuple[str, ...] = ("h.264", "mpeg4", "matroska", "ogg video")
_VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mkv", ".webm", ".ogv", ".avi", ".mov", ".m4v"})
_VIDEO_FORMAT_KEYWORDS: frozenset[str] = frozenset(
    {
        "h.264",
        "mpeg4",
        "mpeg2",
        "matroska",
        "webm",
        "ogg video",
        "avi",
        "quicktime",
    }
)


# --- request / result models ---------------------------------------------


class DownloadRequest(BaseModel):
    archive_id: str
    zone: Zone = Zone.RECOMMENDATIONS
    preferred_formats: list[str] = Field(default_factory=lambda: list(_DEFAULT_FORMATS))
    dest_dir: Path
    dry_run: bool = False


class DownloadResult(BaseModel):
    archive_id: str
    status: Literal["done", "failed", "aborted", "skipped"]
    file_path: Path | None = None
    size_bytes: int | None = None
    format: str | None = None
    duration_s: float = 0.0
    error: str | None = None


# --- format selection ----------------------------------------------------


def _is_video(file_info: dict[str, Any]) -> bool:
    fmt = str(file_info.get("format") or "").lower()
    name = str(file_info.get("name") or "").lower()
    if any(k in fmt for k in _VIDEO_FORMAT_KEYWORDS):
        return True
    return any(name.endswith(ext) for ext in _VIDEO_EXTS)


def pick_format(
    files: list[dict[str, Any]],
    preferred: list[str],
) -> dict[str, Any] | None:
    """Pick the best video file for Roku playback.

    Filters out non-video formats, thumbnails, and metadata files.
    Among the remaining, prefers ``source=="original"`` entries (IA
    derivatives are automatic re-encodes we'd usually rather skip).
    Walks ``preferred`` in order, returning the first match. If no
    preferred format matches, falls back to the first original video.
    """
    videos = [f for f in files if _is_video(f)]
    if not videos:
        return None
    originals = [f for f in videos if str(f.get("source") or "") == "original"]
    pool = originals or videos
    for pref in preferred:
        pref_l = pref.lower()
        for f in pool:
            if pref_l in str(f.get("format") or "").lower():
                return f
    return pool[0]


# --- backend selection ---------------------------------------------------


def _select_backend() -> Literal["ia_get", "library"]:
    """Prefer ia-get if the binary is on PATH. Callers can override via
    a test-time monkeypatch of ``shutil.which``."""
    return "ia_get" if shutil.which("ia-get") else "library"


# --- concurrency governor -----------------------------------------------

# Module-level so phase2-08 (TV sampler) and phase4 (loop) share one
# budget across all downloads in the process. Sized lazily on first use
# so tests can override ``max_concurrent_downloads`` via config.
_SEM_MAX: int | None = None
_SEM: asyncio.Semaphore | None = None


def _semaphore(max_concurrent: int) -> asyncio.Semaphore:
    global _SEM, _SEM_MAX
    if _SEM is None or max_concurrent != _SEM_MAX:
        _SEM = asyncio.Semaphore(max_concurrent)
        _SEM_MAX = max_concurrent
    return _SEM


# --- main entry ----------------------------------------------------------


def _find_existing_row(conn: sqlite3.Connection, archive_id: str) -> sqlite3.Row | None:
    """Return the most recent downloads row for this archive_id, if any."""
    row = conn.execute(
        "SELECT id, status, path, size_bytes FROM downloads "
        "WHERE archive_id = ? ORDER BY id DESC LIMIT 1",
        (archive_id,),
    ).fetchone()
    if row is None:
        return None
    assert isinstance(row, sqlite3.Row)
    return row


async def download_one(
    req: DownloadRequest,
    conn: sqlite3.Connection,
    *,
    max_concurrent: int = 2,
) -> DownloadResult:
    """Download a single Archive.org item into ``req.dest_dir``.

    - If a previous ``done`` row exists for this archive_id, returns
      ``status=skipped`` immediately (idempotent).
    - If a ``failed``/``aborted`` row exists, resets it to ``queued``
      and retries through the current backend.
    - ``dry_run=True`` reports what would happen without writing rows or
      touching the network.
    """
    t0 = time.perf_counter()
    existing = _find_existing_row(conn, req.archive_id)
    if existing is not None and existing["status"] == "done":
        log.info("download_skip_existing", archive_id=req.archive_id)
        return DownloadResult(
            archive_id=req.archive_id,
            status="skipped",
            file_path=Path(existing["path"]) if existing["path"] else None,
            size_bytes=existing["size_bytes"],
            duration_s=time.perf_counter() - t0,
        )

    if req.dry_run:
        log.info("download_dry_run", archive_id=req.archive_id, backend=_select_backend())
        return DownloadResult(
            archive_id=req.archive_id,
            status="skipped",
            duration_s=time.perf_counter() - t0,
        )

    # Insert or reuse queued row
    if existing is not None and existing["status"] in ("failed", "aborted"):
        row_id = int(existing["id"])
        q_downloads.update_progress(conn, row_id, status="queued", error=None)
    else:
        row_id = q_downloads.insert(conn, req.archive_id, req.zone.value)

    async with _semaphore(max_concurrent):
        try:
            backend = _select_backend()
            log.info(
                "download_start",
                archive_id=req.archive_id,
                backend=backend,
                dest_dir=str(req.dest_dir),
            )
            if backend == "ia_get":
                result = await _download_with_ia_get(req, conn, row_id)
            else:
                result = await _download_with_library(req, conn, row_id)
        except Exception as exc:
            duration = time.perf_counter() - t0
            q_downloads.update_progress(
                conn, row_id, status="failed", error=f"{type(exc).__name__}: {exc}"
            )
            log.warning(
                "download_failed",
                archive_id=req.archive_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return DownloadResult(
                archive_id=req.archive_id,
                status="failed",
                duration_s=duration,
                error=str(exc),
            )

    result.duration_s = time.perf_counter() - t0
    log.info(
        "download_complete",
        archive_id=req.archive_id,
        status=result.status,
        size_bytes=result.size_bytes,
        duration_s=result.duration_s,
    )
    return result


# --- library backend (always available) ---------------------------------


async def _get_item_files(archive_id: str) -> list[dict[str, Any]]:
    """Fetch the file list for an Archive.org item. The
    ``internetarchive`` API is blocking; wrap with to_thread."""
    import internetarchive  # lazy import — big C extensions, slow startup

    def _run() -> list[dict[str, Any]]:
        item = internetarchive.get_item(archive_id)
        return list(item.files or [])

    return await asyncio.to_thread(_run)


async def _download_with_library(
    req: DownloadRequest,
    conn: sqlite3.Connection,
    row_id: int,
) -> DownloadResult:
    import internetarchive

    files = await _get_item_files(req.archive_id)
    chosen = pick_format(files, req.preferred_formats)
    if chosen is None:
        q_downloads.update_progress(conn, row_id, status="failed", error="no suitable video file")
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error="no suitable video file",
        )

    req.dest_dir.mkdir(parents=True, exist_ok=True)
    q_downloads.update_progress(conn, row_id, status="downloading")
    file_name = str(chosen["name"])

    def _run_download() -> None:
        # Reach through ``get_item``/``get_file`` for a path the library's
        # type stubs agree with. ``File.download(destdir=...)`` writes
        # ``destdir/<file_name>`` — don't use ``file_path=`` which is
        # interpreted differently across versions.
        item = internetarchive.get_item(req.archive_id)
        file_obj = item.get_file(file_name)
        target_dir = req.dest_dir / req.archive_id
        target_dir.mkdir(parents=True, exist_ok=True)
        file_obj.download(  # type: ignore[no-untyped-call,unused-ignore]
            destdir=str(target_dir),
            retries=3,
        )

    await asyncio.to_thread(_run_download)

    file_path = req.dest_dir / req.archive_id / file_name
    if not file_path.exists():
        q_downloads.update_progress(
            conn, row_id, status="failed", error=f"file missing after download: {file_path}"
        )
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error=f"file missing after download: {file_path}",
        )

    size = file_path.stat().st_size
    q_downloads.update_progress(
        conn,
        row_id,
        status="done",
        path=str(file_path),
        size_bytes=size,
    )
    return DownloadResult(
        archive_id=req.archive_id,
        status="done",
        file_path=file_path,
        size_bytes=size,
        format=str(chosen.get("format") or ""),
    )


# --- ia-get backend (preferred when available) --------------------------


async def _download_with_ia_get(
    req: DownloadRequest,
    conn: sqlite3.Connection,
    row_id: int,
) -> DownloadResult:
    """Shell out to the `ia-get` binary.

    ia-get downloads all files by default; we restrict via ``--include``
    using the chosen file's name pattern. Progress is inferred from the
    final on-disk size rather than parsing stdout — ia-get's progress
    format has changed across versions and polling is trivially portable.
    """
    files = await _get_item_files(req.archive_id)
    chosen = pick_format(files, req.preferred_formats)
    if chosen is None:
        q_downloads.update_progress(conn, row_id, status="failed", error="no suitable video file")
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error="no suitable video file",
        )

    req.dest_dir.mkdir(parents=True, exist_ok=True)
    q_downloads.update_progress(conn, row_id, status="downloading")
    file_name = str(chosen["name"])

    cmd = [
        "ia-get",
        "--output-dir",
        str(req.dest_dir),
        "--resume",
        "--quiet",
        f"--include={file_name}",
        req.archive_id,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace")[:500]
        q_downloads.update_progress(
            conn, row_id, status="failed", error=f"ia-get exit {proc.returncode}: {err}"
        )
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error=f"ia-get exit {proc.returncode}: {err}",
        )

    file_path = req.dest_dir / req.archive_id / file_name
    if not file_path.exists():
        q_downloads.update_progress(
            conn,
            row_id,
            status="failed",
            error=f"ia-get returned 0 but file missing: {file_path}",
        )
        return DownloadResult(
            archive_id=req.archive_id,
            status="failed",
            error=f"ia-get returned 0 but file missing: {file_path}",
        )

    size = file_path.stat().st_size
    q_downloads.update_progress(
        conn,
        row_id,
        status="done",
        path=str(file_path),
        size_bytes=size,
    )
    return DownloadResult(
        archive_id=req.archive_id,
        status="done",
        file_path=file_path,
        size_bytes=size,
        format=str(chosen.get("format") or ""),
    )
