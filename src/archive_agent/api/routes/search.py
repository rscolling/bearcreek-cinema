"""``/search``, ``/search/similar``, ``/search/autocomplete`` — baseline.

Phase4-05 ships the FTS-only slice: ``/search`` always treats the
query as a title intent, ``/similar`` runs cosine from an anchor, and
``/autocomplete`` is a straight FTS prefix lookup. The NL intent
router + live Archive.org fallback land in phase4-08.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import linear_kernel

from archive_agent.api.dependencies import get_db, get_tfidf_index
from archive_agent.api.serializers import (
    AutocompleteSuggestion,
    SearchResultItem,
    to_search_result_item,
)
from archive_agent.ranking.tfidf import TFIDFIndex
from archive_agent.state.models import ContentType, SearchFilter
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import search as q_search
from archive_agent.state.queries import taste_profile_versions as q_profiles

router = APIRouter()

_QueryIntent = Literal["title", "descriptive", "play", "unknown"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    type: Literal["movie", "show", "any"] = "any"


class SearchResponse(BaseModel):
    intent: _QueryIntent
    filter: SearchFilter | None = None
    items: list[SearchResultItem]


class SimilarRequest(BaseModel):
    anchor_archive_id: str
    limit: int = Field(default=10, ge=1, le=50)


class SimilarResponse(BaseModel):
    items: list[SearchResultItem]


class AutocompleteResponse(BaseModel):
    suggestions: list[AutocompleteSuggestion]


def _content_type_from_param(
    type_param: Literal["movie", "show", "any"],
) -> ContentType | None:
    if type_param == "movie":
        return ContentType.MOVIE
    if type_param == "show":
        return ContentType.SHOW
    return None


# --- /search ---------------------------------------------------------------


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SearchResponse:
    ct = _content_type_from_param(req.type)
    hits = q_search.fts_search(conn, req.query, limit=req.limit, content_type=ct)
    items = [
        to_search_result_item(cand, score, match_reason="title match")
        for cand, score in hits
    ]
    return SearchResponse(intent="title", filter=None, items=items)


# --- /search/similar ------------------------------------------------------


@router.post("/search/similar", response_model=SimilarResponse)
async def search_similar(
    req: SimilarRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    index: Annotated[TFIDFIndex, Depends(get_tfidf_index)],
) -> SimilarResponse:
    anchor_row = index.row_for(req.anchor_archive_id)
    if anchor_row is None:
        raise HTTPException(
            status_code=404, detail=f"anchor {req.anchor_archive_id!r} not in index"
        )
    anchor_cand = q_candidates.get_by_archive_id(conn, req.anchor_archive_id)
    anchor_title = anchor_cand.title if anchor_cand else req.anchor_archive_id

    raw = linear_kernel(index.matrix[anchor_row], index.matrix)[0]
    scores = [float(s) for s in raw.tolist()]

    profile = q_profiles.get_latest_profile(conn)
    disliked_archive = (
        set(profile.disliked_archive_ids) if profile else set()
    )
    disliked_shows = (
        set(profile.disliked_show_ids) if profile else set()
    )

    ranking = sorted(
        (
            (i, s)
            for i, s in enumerate(scores)
            if s > 0 and index.archive_ids[i] != req.anchor_archive_id
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    items: list[SearchResultItem] = []
    for idx, score in ranking:
        aid = index.archive_ids[idx]
        if aid in disliked_archive:
            continue
        cand = q_candidates.get_by_archive_id(conn, aid)
        if cand is None:
            continue
        if cand.show_id is not None and cand.show_id in disliked_shows:
            continue
        items.append(
            to_search_result_item(
                cand,
                min(1.0, max(0.0, score)),
                match_reason=f"similar to {anchor_title}",
            )
        )
        if len(items) >= req.limit:
            break

    return SimilarResponse(items=items)


# --- /search/autocomplete -------------------------------------------------


@router.get("/search/autocomplete", response_model=AutocompleteResponse)
async def autocomplete(
    q: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: int = 10,
) -> AutocompleteResponse:
    limit = max(1, min(limit, 50))
    suggestions = q_search.fts_autocomplete(conn, q, limit=limit)
    return AutocompleteResponse(
        suggestions=[
            AutocompleteSuggestion(title=s["title"], archive_id=s["archive_id"])
            for s in suggestions
        ]
    )


__all__ = [
    "AutocompleteResponse",
    "SearchRequest",
    "SearchResponse",
    "SimilarRequest",
    "SimilarResponse",
    "router",
]
