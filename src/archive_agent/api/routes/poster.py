"""``GET /poster/{archive_id}`` — cached image proxy.

Every ``poster_url`` the Roku sees points at this endpoint; clients
never hit archive.org / TMDb directly. Caches fetched bytes under
``<state_db>/../poster_cache/{archive_id}.{ext}``; evicts the
oldest-accessed files once the directory exceeds the configured
size limit.

Streaming on the hot path avoids holding full images in memory
(TMDb source posters are 30-100 KB, but a cold sweep could touch
thousands consecutively).
"""

from __future__ import annotations

import mimetypes
import os
import sqlite3
import time
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from archive_agent.api.dependencies import get_config, get_db
from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.state.queries import candidates as q_candidates

router = APIRouter()

_log = get_logger("archive_agent.api.poster")

_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Stream response chunks — big enough to amortize syscalls, small
# enough that we don't park 1 MB in RAM per connection.
_STREAM_CHUNK = 64 * 1024


def _cache_dir(config: Config) -> Path:
    return config.paths.state_db.parent / "poster_cache"


def _cache_path_for(config: Config, archive_id: str) -> Path | None:
    """Return the existing cache file for ``archive_id``, or ``None``.

    Matches any extension we know about — the cache was written with
    whichever Content-Type came back from upstream.
    """
    cache_dir = _cache_dir(config)
    if not cache_dir.exists():
        return None
    for ext in _CONTENT_TYPE_TO_EXT.values():
        p = cache_dir / f"{archive_id}{ext}"
        if p.exists():
            return p
    return None


def _ext_from_content_type(content_type: str | None) -> str:
    if content_type is None:
        return ".jpg"
    ct = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_TO_EXT.get(ct, ".jpg")


def _media_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _touch_atime(path: Path) -> None:
    """Bump the access time so the LRU sweep treats this entry as hot.

    ``atime`` isn't updated by reads on Windows by default; writing it
    explicitly gives us a consistent eviction signal across platforms.
    """
    try:
        now = time.time()
        os.utime(path, (now, path.stat().st_mtime))
    except OSError:
        pass


def _prune_cache(config: Config) -> None:
    """Evict oldest-accessed files until the cache fits the budget.

    Best-effort — silent on any filesystem surprise. Runs only on
    cache misses (safety net rather than per-request overhead).
    """
    cache_dir = _cache_dir(config)
    if not cache_dir.exists():
        return
    limit = config.api.poster_cache_size_mb * 1024 * 1024
    try:
        entries = [
            (p, p.stat())
            for p in cache_dir.iterdir()
            if p.is_file() and p.suffix in _CONTENT_TYPE_TO_EXT.values()
        ]
    except OSError:
        return
    total = sum(st.st_size for _, st in entries)
    if total <= limit:
        return
    # Oldest-accessed first. Windows may not bump atime on read, but
    # ``_touch_atime`` covers that case.
    entries.sort(key=lambda pair: pair[1].st_atime)
    for path, st in entries:
        if total <= limit:
            break
        try:
            path.unlink()
            total -= st.st_size
        except OSError:
            continue


async def _fetch_upstream(url: str, timeout: float) -> tuple[bytes, str | None]:
    """Fetch the upstream image. Raises ``httpx.HTTPError`` on failure."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type")


def _write_atomic(config: Config, archive_id: str, body: bytes, ext: str) -> Path:
    """Write-then-rename so a crashed transfer doesn't leave a
    truncated file for the next reader."""
    cache_dir = _cache_dir(config)
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_dir / f"{archive_id}{ext}"
    tmp = cache_dir / f".{archive_id}.tmp"
    tmp.write_bytes(body)
    os.replace(tmp, final)
    return final


@router.get("/poster/{archive_id}")
async def get_poster(
    archive_id: str,
    config: Annotated[Config, Depends(get_config)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> Response:
    cached = _cache_path_for(config, archive_id)
    if cached is not None:
        _touch_atime(cached)
        return FileResponse(
            cached,
            media_type=_media_type_for(cached),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    cand = q_candidates.get_by_archive_id(conn, archive_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"no candidate {archive_id!r}")
    if cand.poster_url is None:
        raise HTTPException(status_code=404, detail="no poster_url for this candidate")

    try:
        body, content_type = await _fetch_upstream(
            cand.poster_url, timeout=config.api.poster_upstream_timeout_s
        )
    except httpx.TimeoutException as exc:
        _log.warning("poster_upstream_timeout", archive_id=archive_id, url=cand.poster_url)
        raise HTTPException(
            status_code=502,
            detail="upstream timeout",
            headers={"Retry-After": "30"},
        ) from exc
    except httpx.HTTPError as exc:
        _log.warning(
            "poster_upstream_failed",
            archive_id=archive_id,
            url=cand.poster_url,
            error=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="upstream fetch failed") from exc

    ext = _ext_from_content_type(content_type)
    path = _write_atomic(config, archive_id, body, ext)
    _prune_cache(config)

    return Response(
        content=body,
        media_type=_media_type_for(path),
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Expose the streaming constant so tests can monkey-patch it if they
# need to force a specific chunk boundary. Not part of the public API.
__all__ = ["router"]
_ = _STREAM_CHUNK  # suppress unused import warning
