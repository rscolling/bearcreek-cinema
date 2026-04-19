"""Episode → show grouping heuristics for Archive.org's television
collection.

The collection is messy: sometimes a whole series is one item with
many video files, sometimes each episode is its own item with a
title like ``"The Dick Van Dyke Show S01E03 - Sick Boy"``, sometimes
the title is just the episode name with no show hint at all. This
module classifies each ungrouped EPISODE candidate into one of four
confidence tiers:

- ``high``   — title has an SxEy marker **and** TMDb resolves the
  prefix to a show. Write the show_id / season / episode back to the
  candidate.
- ``medium`` — TMDb resolves the title to a unique show but no SxEy
  marker. Write show_id only (season/episode stay None).
- ``low``    — TMDb returned multiple results, any of which could be
  right. Drop a row in ``tv_grouping_review`` for later manual
  curation; pick the top hit as ``suggested_show_id`` but do not
  write it back to the candidate.
- ``none``   — TMDb had no results, or the title collapsed to
  nothing after marker removal. Review queue only.

Keep the regex conservative (see GUARDRAILS.md's framing around
"never surprise-delete / never wrong-group"). False positives
(grouping into the wrong show) are worse than false negatives
(leaving a loose episode alone).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Literal, NamedTuple

from pydantic import BaseModel

from archive_agent.logging import get_logger
from archive_agent.metadata.tmdb import TmdbClient
from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates

__all__ = [
    "Confidence",
    "GroupingMatch",
    "GroupingResult",
    "SxEy",
    "classify_episode",
    "group_unassigned_episodes",
    "parse_episode_marker",
]

log = get_logger("archive_agent.archive.tv_grouping")

Confidence = Literal["high", "medium", "low", "none"]


class SxEy(NamedTuple):
    season: int
    episode: int
    title_prefix: str  # title with the S/E marker (and trailing punctuation) removed


class GroupingMatch(BaseModel):
    archive_id: str
    show_id: str | None = None
    season: int | None = None
    episode: int | None = None
    confidence: Confidence
    reason: str


class GroupingResult(BaseModel):
    classified: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    none_: int = 0  # `none` shadows the builtin; report as ``none`` in JSON

    def model_dump_for_cli(self) -> dict[str, int]:
        d = self.model_dump()
        d["none"] = d.pop("none_")
        return d


# --- title parsing ------------------------------------------------------

# Full (season + episode) markers, in descending specificity. `\b` isn't
# reliable here because many titles surround these with punctuation that
# contains word-boundary characters (e.g., ``-S01E03-``) — use explicit
# non-alphanum lookarounds instead.
_SXEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # S01E03 / s1e3 / S1E03 etc.
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])[sS](\d{1,2})[eE](\d{1,2})(?:$|[^A-Za-z0-9])"),
    # 1x03 / 01x03
    re.compile(r"(?:^|[^A-Za-z0-9])(\d{1,2})x(\d{1,2})(?:$|[^A-Za-z0-9])"),
    # Season 1 Episode 3 / Season 01 Episode 03
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])Season\s+(\d{1,2})\s+Episode\s+(\d{1,2})(?:$|[^A-Za-z0-9])"),
)

# Episode-only markers — season defaults to 1.
_EPISODE_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # - Ep 03 - / -Ep 3-
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])-\s*Ep\s*(\d{1,2})\s*-"),
    # Episode 3 / episode 03
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])Episode\s+(\d{1,2})(?:$|[^A-Za-z0-9])"),
)

_TRIM_CHARS = frozenset(" -:|()[],." + "\u2014\u2013")  # em-dash, en-dash


def _trim_prefix(raw: str) -> str:
    """Strip trailing separators / whitespace left over after the marker was removed."""
    while raw and raw[-1] in _TRIM_CHARS:
        raw = raw[:-1]
    return raw.strip()


def parse_episode_marker(title: str) -> SxEy | None:
    """Find the first SxEy or episode-only marker in ``title``.

    Returns ``None`` when the title has no recognizable marker.
    ``title_prefix`` is the portion before the marker, with trailing
    separator characters stripped — this is what gets passed to TMDb
    search as the show title.
    """
    if not title:
        return None

    for pat in _SXEY_PATTERNS:
        m = pat.search(title)
        if m:
            prefix = _trim_prefix(title[: m.start()])
            return SxEy(
                season=int(m.group(1)),
                episode=int(m.group(2)),
                title_prefix=prefix,
            )
    for pat in _EPISODE_ONLY_PATTERNS:
        m = pat.search(title)
        if m:
            prefix = _trim_prefix(title[: m.start()])
            return SxEy(season=1, episode=int(m.group(1)), title_prefix=prefix)
    return None


# --- classification -----------------------------------------------------


async def classify_episode(
    candidate: Candidate,
    tmdb: TmdbClient,
) -> GroupingMatch:
    """Walk the four-tier confidence ladder documented in the module
    docstring. Does not mutate ``candidate`` — the caller decides
    whether to write the match back."""
    aid = candidate.archive_id

    # Tier 0: Already grouped by a prior pass or by discovery.
    if candidate.show_id:
        return GroupingMatch(
            archive_id=aid,
            show_id=candidate.show_id,
            season=candidate.season,
            episode=candidate.episode,
            confidence="high",
            reason="already grouped",
        )

    marker = parse_episode_marker(candidate.title)
    search_title = (marker.title_prefix if marker else candidate.title).strip()

    if not search_title:
        return GroupingMatch(
            archive_id=aid,
            confidence="none",
            reason="title empty after removing episode marker",
        )

    results = await tmdb.search_shows(search_title, year=None, limit=5)
    if not results:
        return GroupingMatch(
            archive_id=aid,
            confidence="none",
            reason=f"TMDb: no results for {search_title!r}",
        )

    top = results[0]
    show_id = str(top.id)

    if marker is not None:
        # High: SxEy parsed AND TMDb hit.
        return GroupingMatch(
            archive_id=aid,
            show_id=show_id,
            season=marker.season,
            episode=marker.episode,
            confidence="high",
            reason=f"SxEy + TMDb match: {top.name!r}",
        )

    if len(results) == 1:
        return GroupingMatch(
            archive_id=aid,
            show_id=show_id,
            season=None,
            episode=None,
            confidence="medium",
            reason=f"single TMDb match: {top.name!r}",
        )

    # Multiple TMDb candidates with no SxEy to disambiguate.
    return GroupingMatch(
        archive_id=aid,
        show_id=show_id,  # recorded as suggestion, not written to candidate
        season=None,
        episode=None,
        confidence="low",
        reason=f"{len(results)} TMDb matches (top: {top.name!r})",
    )


# --- DB writeback -------------------------------------------------------


def _record_review(conn: sqlite3.Connection, match: GroupingMatch) -> None:
    conn.execute(
        """
        INSERT INTO tv_grouping_review
            (archive_id, confidence, reason, suggested_show_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(archive_id) DO UPDATE SET
            confidence = excluded.confidence,
            reason = excluded.reason,
            suggested_show_id = excluded.suggested_show_id,
            created_at = excluded.created_at,
            reviewed_at = NULL,
            reviewed_by = NULL
        """,
        (
            match.archive_id,
            match.confidence,
            match.reason,
            match.show_id,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()


async def group_unassigned_episodes(
    conn: sqlite3.Connection,
    tmdb: TmdbClient,
    *,
    limit: int | None = None,
) -> GroupingResult:
    """Classify every EPISODE candidate with ``show_id IS NULL``, apply
    high/medium matches to the candidate row, and record low/none
    matches in the review queue."""
    sql = (
        "SELECT archive_id FROM candidates "
        "WHERE content_type = ? AND show_id IS NULL "
        "ORDER BY discovered_at DESC"
    )
    params: list[object] = [ContentType.EPISODE.value]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    ids = [row["archive_id"] for row in conn.execute(sql, params).fetchall()]

    result = GroupingResult()
    for archive_id in ids:
        candidate = q_candidates.get_by_archive_id(conn, archive_id)
        if candidate is None:
            continue
        match = await classify_episode(candidate, tmdb)
        result.classified += 1
        if match.confidence == "high":
            result.high += 1
        elif match.confidence == "medium":
            result.medium += 1
        elif match.confidence == "low":
            result.low += 1
        else:
            result.none_ += 1

        if match.confidence in ("high", "medium"):
            updated = candidate.model_copy(
                update={
                    "show_id": match.show_id,
                    "season": match.season,
                    "episode": match.episode,
                }
            )
            q_candidates.upsert_candidate(conn, updated)
        else:
            _record_review(conn, match)

    log.info("tv_grouping_complete", **result.model_dump_for_cli())
    return result
