"""Fill in Archive.org gaps using TMDb lookups.

Contract: never overwrite a non-empty field Archive.org already
populated. The user's own library metadata wins if it's there — we're
strictly a fallback/supplement.

Movies → ``/search/movie`` + ``/movie/{id}`` for runtime and full genres.
Shows/episodes → ``/search/tv`` + ``/tv/{id}`` for ``episode_run_time``
(TMDb returns a list; we take the first value).
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from archive_agent.logging import get_logger
from archive_agent.metadata.models import TmdbMovie, TmdbShow
from archive_agent.metadata.tmdb import TmdbClient, TmdbError
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates

__all__ = ["EnrichResult", "enrich_candidate", "enrich_new_candidates"]

log = get_logger("archive_agent.metadata.enrich")


class EnrichResult(BaseModel):
    seen: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_tmdb_match: int = 0
    failed: int = 0


async def enrich_candidate(
    candidate: Candidate,
    client: TmdbClient,
) -> Candidate:
    """Return an enriched copy. Fields Archive.org already filled are
    never overwritten; the rest may be populated from TMDb. Returns the
    original candidate unchanged if TMDb has no match."""
    is_movie = candidate.content_type is ContentType.MOVIE
    details: TmdbMovie | TmdbShow
    runtime: int | None
    if is_movie:
        movie_hit = await client.search_movie(candidate.title, candidate.year)
        if movie_hit is None:
            return candidate
        details = await client.get_movie(movie_hit.id)
        runtime = details.runtime
    else:
        show_hit = await client.search_show(candidate.title, candidate.year)
        if show_hit is None:
            return candidate
        details = await client.get_show(show_hit.id)
        runtime = details.episode_run_time[0] if details.episode_run_time else None

    # Resolved genre names from the by-id response (search uses genre_ids)
    genres = [g.name.lower() for g in details.genres]
    poster_url = await client.build_poster_url(details.poster_path)

    updates: dict[str, object] = {}
    if not candidate.genres and genres:
        updates["genres"] = sorted(set(genres))
    if candidate.runtime_minutes is None and runtime:
        updates["runtime_minutes"] = runtime
    if not candidate.description and details.overview:
        updates["description"] = details.overview
    if candidate.poster_url is None and poster_url:
        updates["poster_url"] = poster_url

    if not updates:
        return candidate
    return candidate.model_copy(update=updates)


async def enrich_new_candidates(
    conn: sqlite3.Connection,
    client: TmdbClient,
    *,
    limit: int | None = None,
) -> EnrichResult:
    """Enrich NEW candidates that are missing at least one of
    ``genres``, ``poster_url``, or ``description``.

    Returns per-candidate counters. Failures are logged and counted;
    one bad candidate doesn't abort the rest of the batch.
    """
    sql = (
        "SELECT archive_id FROM candidates "
        "WHERE status = ? AND (genres = '[]' OR poster_url IS NULL OR description = '') "
        "ORDER BY discovered_at DESC"
    )
    params: list[object] = [CandidateStatus.NEW.value]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    ids = [row["archive_id"] for row in conn.execute(sql, params).fetchall()]

    result = EnrichResult()
    for archive_id in ids:
        candidate = q_candidates.get_by_archive_id(conn, archive_id)
        if candidate is None:
            continue
        result.seen += 1
        try:
            enriched = await enrich_candidate(candidate, client)
        except TmdbError as exc:
            log.warning("enrich_tmdb_error", archive_id=archive_id, error=str(exc))
            result.failed += 1
            continue
        except Exception as exc:
            log.warning(
                "enrich_unexpected_error",
                archive_id=archive_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            result.failed += 1
            continue
        if enriched is candidate or enriched == candidate:
            # TMDb returned no match OR nothing to fill — either way no write.
            if enriched is candidate:
                result.missing_tmdb_match += 1
            else:
                result.unchanged += 1
            continue
        q_candidates.upsert_candidate(conn, enriched)
        result.updated += 1

    log.info("enrich_complete", **result.model_dump())
    return result
