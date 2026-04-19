"""Thin wrapper around ``internetarchive.search_items`` for the two
collections we care about.

Normalizes the raw Archive.org dict into an ``ArchiveSearchResult``
that downstream code can rely on:

- ``runtime`` on Archive.org is freeform. We parse ``H:MM:SS`` /
  ``MM:SS`` / ``Approx NN Minutes`` / ``NN min`` and give up cleanly
  (None) on everything else.
- ``subject`` is sometimes a string, sometimes a list. We coerce to
  list.
- ``year`` is sometimes an int, sometimes a string, sometimes absent.
- Values we don't read are ignored.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from internetarchive import search_items
from pydantic import BaseModel, ConfigDict, Field, field_validator

from archive_agent.logging import get_logger

__all__ = [
    "ArchiveCollection",
    "ArchiveSearchResult",
    "parse_runtime_minutes",
    "search_collection",
]

log = get_logger("archive_agent.archive.search")

ArchiveCollection = Literal["moviesandfilms", "television"]

_IA_FIELDS = [
    "identifier",
    "title",
    "mediatype",
    "year",
    "downloads",
    "runtime",
    "subject",
    "description",
    "format",
]

# H:MM:SS or H:MM or MM:SS depending on length. Archive.org is inconsistent.
_HMS_RE = re.compile(r"^\s*(?P<a>\d+):(?P<b>\d+)(?::(?P<c>\d+))?\s*$")
# "Approx 30 Minutes", "30 Minutes", "30 min", "90 minutes"
_MIN_RE = re.compile(r"(?:approx\.?\s+)?(\d+)\s*(?:min|minutes?)\b", re.IGNORECASE)


def parse_runtime_minutes(raw: str | int | None) -> int | None:
    """Best-effort parse of Archive.org's freeform runtime field.

    Returns integer minutes on success; ``None`` when the value is
    missing or in a format we don't recognize.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    s = str(raw).strip()
    if not s:
        return None
    m = _HMS_RE.match(s)
    if m:
        a, b, c = int(m["a"]), int(m["b"]), int(m["c"] or 0)
        # H:MM:SS when all three are present. Otherwise MM:SS.
        total_sec = a * 3600 + b * 60 + c if m["c"] is not None else a * 60 + b
        return max(1, total_sec // 60)
    mm = _MIN_RE.search(s)
    if mm:
        return int(mm.group(1))
    return None


class ArchiveSearchResult(BaseModel):
    """Normalized row from an Archive.org search."""

    model_config = ConfigDict(extra="ignore")

    identifier: str
    title: str
    mediatype: str = ""
    year: int | None = None
    downloads: int | None = None
    runtime_minutes: int | None = None
    subject: list[str] = Field(default_factory=list)
    description: str = ""
    formats: list[str] = Field(default_factory=list)

    @field_validator("subject", mode="before")
    @classmethod
    def _coerce_subject_to_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v]
        return [str(v)]

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


def _build_query(
    collection: ArchiveCollection,
    *,
    min_downloads: int,
    year_from: int,
    year_to: int,
) -> str:
    parts = [
        f"collection:{collection}",
        f"year:[{year_from} TO {year_to}]",
        f"downloads:[{min_downloads} TO 99999999]",
    ]
    # The moviesandfilms collection is exclusively mediatype=movies; the
    # television collection mixes a little too (the API is inconsistent).
    # Restrict to movies/movingimage to keep out non-video items.
    parts.append("(mediatype:movies OR mediatype:movingimage)")
    return " AND ".join(parts)


def _raw_to_result(raw: dict[str, Any]) -> ArchiveSearchResult:
    """Map a single search dict to our Pydantic model.

    Route through ``model_validate`` so the field_validators handle
    coercion (``subject`` scalar-or-list, ``year`` int-or-string). The
    keys that differ from Archive.org's shape are remapped here.
    """
    payload: dict[str, Any] = {
        "identifier": str(raw.get("identifier", "")),
        "title": str(raw.get("title", "")),
        "mediatype": str(raw.get("mediatype", "")),
        "year": raw.get("year"),
        "downloads": raw.get("downloads"),
        "runtime_minutes": parse_runtime_minutes(raw.get("runtime")),
        "subject": raw.get("subject"),
        "description": str(raw.get("description", "")),
        "formats": raw.get("format") or [],
    }
    return ArchiveSearchResult.model_validate(payload)


async def search_collection(
    collection: ArchiveCollection,
    *,
    min_downloads: int,
    year_from: int,
    year_to: int,
    limit: int | None = None,
    page_size: int = 100,
) -> AsyncIterator[ArchiveSearchResult]:
    """Yield normalized Archive.org search results.

    The underlying ``internetarchive.search_items`` iterator is sync
    and does its own HTTP; we run it on a thread so it doesn't stall
    the event loop. ``limit=None`` means "all results" — the caller
    should bound it (the CLI defaults to 100).
    """
    query = _build_query(
        collection,
        min_downloads=min_downloads,
        year_from=year_from,
        year_to=year_to,
    )
    log.info(
        "archive_search_start",
        collection=collection,
        query=query,
        limit=limit,
    )

    def _run() -> list[dict[str, Any]]:
        # search_items is lazy; we collect pages up to ``limit`` here so
        # the thread exits cleanly rather than holding a cursor open.
        out: list[dict[str, Any]] = []
        it = search_items(query, fields=_IA_FIELDS, params={"rows": page_size})
        for raw in it:
            out.append(dict(raw))
            if limit is not None and len(out) >= limit:
                break
        return out

    raws = await asyncio.to_thread(_run)
    log.info(
        "archive_search_complete",
        collection=collection,
        returned=len(raws),
    )
    for raw in raws:
        yield _raw_to_result(raw)
