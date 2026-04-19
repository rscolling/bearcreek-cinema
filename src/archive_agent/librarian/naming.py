"""Jellyfin-compatible filename and folder construction.

Pure functions — the tests are the spec. Sanitization strips the
characters Windows and many Linux filesystems disallow, collapses
whitespace, and trims trailing dots/spaces that Windows treats as
errors at the filesystem layer. Hand-rolled to avoid adding a new
dependency; if edge cases pile up later, swap in ``pathvalidate``
behind an ADR.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "disambiguate",
    "disambiguate_folder",
    "jellyfin_episode_filename",
    "jellyfin_movie_filename",
    "jellyfin_movie_folder",
    "jellyfin_season_folder",
    "jellyfin_show_folder",
    "sanitize_filename",
]

# Windows-forbidden characters + ASCII control chars. Also strips forward
# and backward slashes (which are path separators) so a malicious title
# can't escape its folder.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_filename(name: str) -> str:
    """Return ``name`` with filesystem-unsafe chars replaced by spaces,
    whitespace collapsed, and trailing dots / spaces stripped."""
    if not name:
        return ""
    cleaned = _UNSAFE.sub(" ", name)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    return cleaned


def jellyfin_movie_folder(title: str, year: int | None) -> str:
    """``Sita Sings the Blues (2008)`` — Jellyfin resolves this folder
    shape cleanly to TMDb metadata."""
    safe = sanitize_filename(title) or "Unknown Title"
    if year is not None:
        return f"{safe} ({year})"
    return safe


def jellyfin_movie_filename(title: str, year: int | None, ext: str) -> str:
    """File inside the movie folder uses the same stem so Jellyfin
    scans deterministically, and the extension preserves the codec."""
    return jellyfin_movie_folder(title, year) + ext


def jellyfin_show_folder(show_title: str) -> str:
    return sanitize_filename(show_title) or "Unknown Show"


def jellyfin_season_folder(season: int) -> str:
    return f"Season {season:02d}"


def jellyfin_episode_filename(
    show_title: str,
    season: int,
    episode: int,
    ep_title: str | None,
    ext: str,
) -> str:
    """``Dick Van Dyke Show - S01E03 - Sick Boy and Sore Loser.mp4``"""
    show = sanitize_filename(show_title) or "Unknown Show"
    base = f"{show} - S{season:02d}E{episode:02d}"
    if ep_title:
        ep_safe = sanitize_filename(ep_title)
        if ep_safe and ep_safe.lower() != show.lower():
            base += f" - {ep_safe}"
    return base + ext


def disambiguate(dest: Path, *, max_attempts: int = 99) -> Path:
    """Return a path that doesn't already exist, by appending ``(N)``."""
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for n in range(1, max_attempts + 1):
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many existing variants of {dest}")


def disambiguate_folder(dest: Path, *, max_attempts: int = 99) -> Path:
    """Folder-flavor of :func:`disambiguate` — same logic but no suffix."""
    if not dest.exists():
        return dest
    parent = dest.parent
    for n in range(1, max_attempts + 1):
        candidate = parent / f"{dest.name} ({n})"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many existing variants of {dest}")
