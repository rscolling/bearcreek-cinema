"""Jellyfin library scan + item-id linkage (phase2-09).

After ``librarian.place`` or ``librarian.promote_*`` lands a file in a
``/media/*`` zone, this module triggers the corresponding Jellyfin
library scan and polls for the new item until Jellyfin finishes
indexing. The resolved ``ItemId`` (a Jellyfin GUID) gets written back
onto ``candidates.jellyfin_item_id`` — that's what the Roku app
deep-links into for playback.

Library resolution uses ``/Library/VirtualFolders`` (admin endpoint)
to match expected zone paths to Jellyfin library ids. The agent never
creates libraries itself (GUARDRAILS: don't modify Jellyfin's config);
the user has to create "Recommendations" and "TV Sampler" custom
libraries at first deploy. See ``ENVIRONMENT.md`` for the checklist.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from archive_agent.jellyfin.client import JellyfinClient
from archive_agent.jellyfin.models import JellyfinItem
from archive_agent.librarian.zones import Zone
from archive_agent.logging import get_logger
from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates

__all__ = [
    "LibraryMap",
    "MissingLibraryError",
    "resolve_libraries",
    "scan_and_resolve",
    "scan_zones",
]

log = get_logger("archive_agent.jellyfin.placement")

_EXPECTED_PATHS = {
    Zone.MOVIES: "/media/movies",
    Zone.TV: "/media/tv",
    Zone.RECOMMENDATIONS: "/media/recommendations",
    Zone.TV_SAMPLER: "/media/tv-sampler",
}

_WHITESPACE_RE = re.compile(r"\s+")


class MissingLibraryError(Exception):
    """Raised when Jellyfin doesn't have the libraries we need for
    every zone. Includes a user-facing message that names the missing
    paths + the dashboard location where they should be created."""


class LibraryMap(BaseModel):
    """Jellyfin library ids keyed by agent-managed zone."""

    movies: str
    tv: str
    recommendations: str
    tv_sampler: str

    def library_id(self, zone: Zone) -> str:
        match zone:
            case Zone.MOVIES:
                return self.movies
            case Zone.TV:
                return self.tv
            case Zone.RECOMMENDATIONS:
                return self.recommendations
            case Zone.TV_SAMPLER:
                return self.tv_sampler


# --- library resolution -------------------------------------------------


def _normalize_path(path: str) -> str:
    """Lowercase + drop trailing slash + convert to forward slashes."""
    return str(PurePosixPath(path.replace("\\", "/").rstrip("/"))).lower()


def _match_zone_to_folders(
    virtual_folders: list[dict[str, Any]],
) -> dict[Zone, str]:
    """Walk the ``/Library/VirtualFolders`` response and return a
    ``{Zone: ItemId}`` map for every matching zone. Partial matches
    are fine — missing-library detection happens at the caller."""
    expected_norm = {zone: _normalize_path(p) for zone, p in _EXPECTED_PATHS.items()}
    found: dict[Zone, str] = {}
    for folder in virtual_folders:
        item_id = folder.get("ItemId") or folder.get("Id") or ""
        if not item_id:
            continue
        for loc in folder.get("Locations") or []:
            loc_norm = _normalize_path(str(loc))
            for zone, path_norm in expected_norm.items():
                if loc_norm == path_norm and zone not in found:
                    found[zone] = str(item_id)
    return found


async def resolve_libraries(client: JellyfinClient) -> LibraryMap:
    """List virtual folders, match by filesystem path, return the map.

    Raises :class:`MissingLibraryError` when any of the four expected
    libraries (Movies, TV, Recommendations, TV Sampler) isn't present
    — we don't silently skip zones the user hasn't configured."""
    data = await client.raw_get("/Library/VirtualFolders")
    # Jellyfin returns a list of folder dicts. Older versions used
    # ``ItemId``, newer use ``Id``; _match_zone_to_folders tries both.
    folders: list[dict[str, Any]]
    if isinstance(data, list):
        folders = [dict(d) for d in data]
    elif isinstance(data, dict):
        folders = [data]
    else:
        folders = []

    found = _match_zone_to_folders(folders)
    missing = [
        _EXPECTED_PATHS[z]
        for z in (Zone.MOVIES, Zone.TV, Zone.RECOMMENDATIONS, Zone.TV_SAMPLER)
        if z not in found
    ]
    if missing:
        raise MissingLibraryError(
            f"Jellyfin is missing libraries for these paths: {missing}. "
            "Create them via Dashboard → Libraries → Add Media Library. "
            "See claude-code-pack/ENVIRONMENT.md for the setup checklist."
        )
    return LibraryMap(
        movies=found[Zone.MOVIES],
        tv=found[Zone.TV],
        recommendations=found[Zone.RECOMMENDATIONS],
        tv_sampler=found[Zone.TV_SAMPLER],
    )


# --- matching ----------------------------------------------------------


def _norm_title(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.strip().lower())


def _titles_match(jellyfin_title: str, candidate_title: str) -> bool:
    return _norm_title(jellyfin_title) == _norm_title(candidate_title)


async def _find_item_for_candidate(
    client: JellyfinClient,
    library_id: str,
    candidate: Candidate,
) -> JellyfinItem | None:
    """Search a specific Jellyfin library for the item matching the
    candidate. Scoping by ``ParentId=<library_id>`` is what keeps us
    from matching a copy of the same film in a different zone."""
    if candidate.content_type == ContentType.MOVIE:
        page = await client.list_items(
            library_id=library_id,
            include_item_types=["Movie"],
            fields=["ProductionYear"],
            limit=1000,
        )
        for item in page.items:
            if not _titles_match(item.name, candidate.title):
                continue
            if candidate.year is not None and item.production_year != candidate.year:
                continue
            return item
        return None

    # Episode / show
    page = await client.list_items(
        library_id=library_id,
        include_item_types=["Episode"],
        fields=["ParentIndexNumber", "IndexNumber", "SeriesName"],
        limit=1000,
    )
    for item in page.items:
        if candidate.season is not None and item.parent_index_number != candidate.season:
            continue
        if candidate.episode is not None and item.index_number != candidate.episode:
            continue
        return item
    return None


# --- scan + resolve ----------------------------------------------------


async def scan_and_resolve(
    client: JellyfinClient,
    conn: sqlite3.Connection,
    *,
    archive_id: str,
    zone: Zone,
    timeout_s: int = 90,
    poll_interval_s: float = 2.0,
) -> str | None:
    """Trigger a library scan, poll for the new item, persist the
    resolved ItemId on the candidate, return it.

    Returns ``None`` if the timeout elapsed — logs a WARN but doesn't
    raise, because a timeout often means Jellyfin is still indexing
    (the background scan might finish later and a later call resolves
    cleanly). Callers decide whether to retry.
    """
    candidate = q_candidates.get_by_archive_id(conn, archive_id)
    if candidate is None:
        raise ValueError(f"no candidate with archive_id={archive_id!r}")

    libs = await resolve_libraries(client)
    library_id = libs.library_id(zone)
    await client.trigger_library_scan(library_id)
    log.info(
        "jellyfin_scan_triggered",
        archive_id=archive_id,
        zone=zone.value,
        library_id=library_id,
    )

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        item = await _find_item_for_candidate(client, library_id, candidate)
        if item is not None:
            q_candidates.upsert_candidate(
                conn, candidate.model_copy(update={"jellyfin_item_id": item.id})
            )
            log.info(
                "jellyfin_item_resolved",
                archive_id=archive_id,
                jellyfin_item_id=item.id,
                zone=zone.value,
            )
            return item.id
        await asyncio.sleep(poll_interval_s)

    log.warning(
        "jellyfin_scan_resolve_timeout",
        archive_id=archive_id,
        zone=zone.value,
        timeout_s=timeout_s,
    )
    return None


async def scan_zones(client: JellyfinClient, zones: list[Zone]) -> None:
    """Trigger one library scan per zone. Dedupes — passing the same
    zone twice is a no-op."""
    libs = await resolve_libraries(client)
    seen: set[Zone] = set()
    for zone in zones:
        if zone in seen:
            continue
        seen.add(zone)
        await client.trigger_library_scan(libs.library_id(zone))
        log.info("jellyfin_scan_triggered", zone=zone.value)
