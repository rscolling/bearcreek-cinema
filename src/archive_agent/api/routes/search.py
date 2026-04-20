"""``/search`` with intent routing + ``/search/similar`` + ``/search/autocomplete``.

Phase4-08 replaces phase4-05's always-title ``/search`` with a
router-dispatched endpoint:

- ``TITLE`` / ``PLAY_COMMAND`` → FTS5 over titles + descriptions.
- ``DESCRIPTIVE`` (no anchor) → cosine scoring against a synthetic
  profile built from the query tokens (no LLM call on the hot path;
  the TF-IDF provider's intent is "retrieve, then rank later").
- ``DESCRIPTIVE`` with an anchor (``"more like X"``) → FTS-resolve the
  anchor string, then run the ``/search/similar`` pipeline.
- ``UNKNOWN`` → falls back to title FTS.

The dedicated LLM classifier (``llama3.2:3b``) and the live
Archive.org fallback are seamed but deferred — see router.py and
the phase4-08 card.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
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
from archive_agent.ranking.tfidf import TFIDFIndex, prefilter
from archive_agent.search import QueryIntent, route_query
from archive_agent.state.models import (
    Candidate,
    ContentType,
    SearchFilter,
    TasteProfile,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import search as q_search
from archive_agent.state.queries import taste_profile_versions as q_profiles

router = APIRouter()

# How confident a top-1 FTS hit must be to short-circuit a short
# query to TITLE without touching the LLM stage. Normalized bm25
# scoring from state.queries.search puts strong matches above 0.5.
_FTS_PROBE_MIN = 0.5


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    type: Literal["movie", "show", "any"] = "any"


class SearchResponse(BaseModel):
    intent: str
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


# --- similarity core ------------------------------------------------------


def _similar_to_anchor(
    conn: sqlite3.Connection,
    index: TFIDFIndex,
    *,
    anchor_archive_id: str,
    limit: int,
    match_reason: str,
) -> list[SearchResultItem]:
    """Return candidates ranked by cosine similarity to ``anchor``.

    Shared by ``/search/similar`` and the ``DESCRIPTIVE + anchor``
    dispatch. Excludes the anchor itself and anything in the latest
    profile's disliked lists.
    """
    anchor_row = index.row_for(anchor_archive_id)
    if anchor_row is None:
        return []

    raw = linear_kernel(index.matrix[anchor_row], index.matrix)[0]
    scores = [float(s) for s in raw.tolist()]

    profile = q_profiles.get_latest_profile(conn)
    disliked_archive = set(profile.disliked_archive_ids) if profile else set()
    disliked_shows = set(profile.disliked_show_ids) if profile else set()

    ranking = sorted(
        (
            (i, s)
            for i, s in enumerate(scores)
            if s > 0 and index.archive_ids[i] != anchor_archive_id
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
                match_reason=match_reason,
            )
        )
        if len(items) >= limit:
            break
    return items


# --- dispatch helpers ------------------------------------------------------


def _title_hits(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    content_type: ContentType | None,
    match_reason: str,
) -> list[SearchResultItem]:
    cleaned = _fts_safe(query)
    if not cleaned:
        return []
    hits = q_search.fts_search(conn, cleaned, limit=limit, content_type=content_type)
    return [to_search_result_item(cand, score, match_reason=match_reason) for cand, score in hits]


def _descriptive_hits(
    conn: sqlite3.Connection,
    index: TFIDFIndex,
    query: str,
    *,
    limit: int,
    content_type: ContentType | None,
) -> list[SearchResultItem]:
    """Treat the query itself as a synthetic taste profile.

    We don't want descriptive intent to bleed the household's actual
    liked_genres into the ranking — the user asked for something
    specific. Build a throwaway profile whose only positive signal
    is the query terms + any era facets picked up along the way.
    """
    synthetic = TasteProfile(
        version=0,
        updated_at=datetime.now(UTC),
        summary=query,
        liked_genres=[t for t in query.split() if len(t) >= 3],
    )
    picks = prefilter(
        index, conn, synthetic, k=limit, content_types=[content_type] if content_type else None
    )
    return [
        to_search_result_item(
            cand,
            score,
            match_reason=_descriptive_reason(cand, query),
        )
        for cand, score in picks
    ]


def _descriptive_reason(cand: Candidate, query: str) -> str:
    """Pick a genre overlap for the match_reason if we can, else a
    generic fallback. Keeps the Roku detail string concrete."""
    tokens = {t.lower() for t in query.split()}
    shared = [g for g in cand.genres if g.lower() in tokens]
    if shared:
        return f"matches: {', '.join(shared[:2])}"
    return "descriptive match"


def _fts_safe(s: str) -> str:
    """Return ``s`` as space-joined alnum tokens only.

    User-typed queries can contain ``-`` / ``"`` / ``*`` which FTS5
    reads as operators and rejects at parse time. Collapsing to
    alnum+spaces lets bare tokens match without changing FTS
    semantics for the common case.
    """
    import re as _re

    tokens = _re.findall(r"[A-Za-z0-9]+", s)
    return " ".join(tokens)


def _resolve_anchor_archive_id(conn: sqlite3.Connection, anchor_query: str) -> str | None:
    """FTS-resolve "more like X" to the best-matching archive_id.

    Returns ``None`` when there's no plausible title hit.
    """
    cleaned = _fts_safe(anchor_query)
    if not cleaned:
        return None
    hits = q_search.fts_search(conn, cleaned, limit=1)
    if not hits:
        return None
    return hits[0][0].archive_id


# --- /search ---------------------------------------------------------------


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    index: Annotated[TFIDFIndex, Depends(get_tfidf_index)],
) -> SearchResponse:
    ct = _content_type_from_param(req.type)

    def _fts_probe(q: str) -> bool:
        cleaned = _fts_safe(q)
        if not cleaned:
            return False
        hits = q_search.fts_search(conn, cleaned, limit=1)
        return bool(hits and hits[0][1] >= _FTS_PROBE_MIN)

    route = await route_query(req.query, fts_probe=_fts_probe)

    # Descriptive with anchor → resolve then run similar.
    if route.intent == QueryIntent.DESCRIPTIVE and route.anchor_query:
        anchor_id = _resolve_anchor_archive_id(conn, route.anchor_query)
        if anchor_id is None:
            return SearchResponse(intent=route.intent.value, filter=route.filter, items=[])
        items = _similar_to_anchor(
            conn,
            index,
            anchor_archive_id=anchor_id,
            limit=req.limit,
            match_reason=f"similar to {route.anchor_query}",
        )
        return SearchResponse(intent=route.intent.value, filter=route.filter, items=items)

    # Descriptive without anchor → TF-IDF scoring against the query.
    if route.intent == QueryIntent.DESCRIPTIVE:
        items = _descriptive_hits(
            conn, index, route.normalized_query, limit=req.limit, content_type=ct
        )
        return SearchResponse(intent=route.intent.value, filter=route.filter, items=items)

    # Play command — strip the verb, treat the remainder as a title.
    if route.intent == QueryIntent.PLAY_COMMAND and route.stripped_query:
        items = _title_hits(
            conn,
            route.stripped_query,
            limit=req.limit,
            content_type=ct,
            match_reason="play-command title match",
        )
        return SearchResponse(intent=route.intent.value, filter=route.filter, items=items)

    # TITLE (explicit or default fallback) + UNKNOWN → FTS path.
    items = _title_hits(
        conn,
        route.normalized_query,
        limit=req.limit,
        content_type=ct,
        match_reason="title match",
    )
    return SearchResponse(intent=route.intent.value, filter=route.filter, items=items)


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

    items = _similar_to_anchor(
        conn,
        index,
        anchor_archive_id=req.anchor_archive_id,
        limit=req.limit,
        match_reason=f"similar to {anchor_title}",
    )
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
