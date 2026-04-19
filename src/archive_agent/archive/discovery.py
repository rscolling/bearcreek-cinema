"""High-level Archive.org discovery: query both collections, map
results to Pydantic ``Candidate`` rows, upsert into the state DB.

Idempotent — re-running ``discover`` on the same query updates
existing rows rather than duplicating (the primary key is
``archive_id``). The ``status`` column is left untouched for
already-ranked candidates so subsequent runs don't clobber downstream
state machinery.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from archive_agent.archive.search import (
    ArchiveCollection,
    ArchiveSearchResult,
    search_collection,
)
from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates

__all__ = ["DiscoverResult", "discover", "search_result_to_candidate"]

log = get_logger("archive_agent.archive.discovery")


class DiscoverResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped_quality: int = 0
    skipped_year: int = 0
    by_collection: dict[str, int] = Field(default_factory=dict)


def _content_type_for(collection: ArchiveCollection) -> ContentType:
    # phase2-03 will reclassify television items into SHOW where it can
    # group them. This phase marks every television row as EPISODE so
    # the ungrouped ones stay visible.
    if collection == "moviesandfilms":
        return ContentType.MOVIE
    return ContentType.EPISODE


def search_result_to_candidate(
    result: ArchiveSearchResult,
    *,
    source_collection: ArchiveCollection,
) -> Candidate:
    """Convert a normalized search result into a ``Candidate``.

    Heuristics:
    - Content type is driven by the collection (see comment above).
    - Genres are the lowercased, deduped ``subject`` list.
    - ``discovered_at`` is always "now" even on re-discovery so the
      field reflects the most recent time we saw the item (useful for
      debugging stale catalog state).
    """
    genres = sorted({s.strip().lower() for s in result.subject if s and s.strip()})
    return Candidate(
        archive_id=result.identifier,
        content_type=_content_type_for(source_collection),
        title=result.title,
        year=result.year,
        runtime_minutes=result.runtime_minutes,
        genres=genres,
        description=result.description,
        formats_available=list(result.formats),
        source_collection=source_collection,
        status=CandidateStatus.NEW,
        discovered_at=datetime.now(UTC),
    )


def _merge_status(existing: Candidate | None, fresh: Candidate) -> Candidate:
    """Preserve the existing ``status`` unless it's ``NEW`` (in which
    case the fresh row's NEW is a no-op anyway). Prevents
    re-discovery from rolling back a row that's already progressed
    through DOWNLOADING / COMMITTED / REJECTED / etc."""
    if existing is None:
        return fresh
    return fresh.model_copy(update={"status": existing.status})


async def _discover_one_collection(
    conn: sqlite3.Connection,
    config: Config,
    collection: ArchiveCollection,
    limit: int | None,
    result: DiscoverResult,
) -> None:
    archive = config.archive
    seen_here = 0
    async for raw in search_collection(
        collection,
        min_downloads=archive.min_download_count,
        year_from=archive.year_from,
        year_to=archive.year_to,
        limit=limit,
    ):
        if raw.downloads is not None and raw.downloads < archive.min_download_count:
            result.skipped_quality += 1
            continue
        if raw.year is not None and not (archive.year_from <= raw.year <= archive.year_to):
            result.skipped_year += 1
            continue
        fresh = search_result_to_candidate(raw, source_collection=collection)
        existing = q_candidates.get_by_archive_id(conn, fresh.archive_id)
        merged = _merge_status(existing, fresh)
        q_candidates.upsert_candidate(conn, merged)
        if existing is None:
            result.inserted += 1
        else:
            result.updated += 1
        seen_here += 1
    result.by_collection[collection] = seen_here


async def discover(
    conn: sqlite3.Connection,
    config: Config,
    collection: Literal["moviesandfilms", "television", "both"] = "both",
    limit: int | None = None,
) -> DiscoverResult:
    """Run discovery for the requested collection(s).

    ``limit`` applies per-collection. ``collection='both'`` queries
    them sequentially (Archive.org throttles shared between them, so
    running concurrently buys nothing and complicates error handling).
    """
    result = DiscoverResult()
    collections: list[ArchiveCollection] = (
        ["moviesandfilms", "television"] if collection == "both" else [collection]
    )
    for c in collections:
        await _discover_one_collection(conn, config, c, limit, result)
    log.info("discover_complete", **result.model_dump())
    return result
