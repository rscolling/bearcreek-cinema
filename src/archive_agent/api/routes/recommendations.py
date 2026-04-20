"""``/recommendations*`` endpoints.

The daemon loop produces batches on a schedule (``run_if_due`` for
the profile + ``recommend()`` for the list); these endpoints never
trigger a fresh LLM call on the request path. They only read the
``latest_batch`` and write taste events for reject / defer.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from archive_agent.api.dependencies import get_db
from archive_agent.api.serializers import (
    RecommendationItem,
    to_recommendation_item,
)
from archive_agent.state.models import (
    CandidateStatus,
    ContentType,
    TasteEvent,
    TasteEventKind,
)
from archive_agent.state.queries import candidates as q_candidates
from archive_agent.state.queries import ranked_candidates as q_ranked
from archive_agent.state.queries import taste_events as q_taste_events

router = APIRouter()


class RecommendationsResponse(BaseModel):
    items: list[RecommendationItem]


# --- list ------------------------------------------------------------------


@router.get("/recommendations", response_model=RecommendationsResponse)
async def list_recommendations(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    type: Literal["movie", "show", "any"] = "any",
    limit: int = 10,
) -> RecommendationsResponse:
    batch = q_ranked.latest_batch(conn)
    items = [to_recommendation_item(r, conn) for r in batch]
    if type == "movie":
        items = [i for i in items if i.content_type == ContentType.MOVIE]
    elif type == "show":
        items = [i for i in items if i.content_type == ContentType.SHOW]
    return RecommendationsResponse(items=items[:limit])


@router.get("/recommendations/for-tonight", response_model=RecommendationsResponse)
async def for_tonight(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> RecommendationsResponse:
    """Three picks weighted by local time-of-day.

    Evening (17-22) → prefer feature-length; late night (22-02) →
    prefer short content. Outside those windows, no filter.
    """
    batch = q_ranked.latest_batch(conn)
    all_items = [to_recommendation_item(r, conn) for r in batch]
    hour = datetime.now().hour

    def is_evening() -> bool:
        return 17 <= hour < 22

    def is_late_night() -> bool:
        return hour >= 22 or hour < 2

    filtered: list[RecommendationItem]
    if is_evening():
        filtered = [i for i in all_items if (i.runtime_minutes or 0) >= 90] or all_items
    elif is_late_night():
        filtered = [i for i in all_items if (i.runtime_minutes or 999) <= 60] or all_items
    else:
        filtered = all_items
    return RecommendationsResponse(items=filtered[:3])


# --- reject / defer --------------------------------------------------------


def _insert_signal_event(
    conn: sqlite3.Connection,
    archive_id: str,
    *,
    kind: TasteEventKind,
    strength: float,
) -> None:
    cand = q_candidates.get_by_archive_id(conn, archive_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"no candidate {archive_id!r}")
    if cand.content_type == ContentType.EPISODE:
        # Episodes aren't first-class taste events (ADR-004). Route
        # episode-level dislikes through the show_id if we have one,
        # else refuse rather than silently swallow.
        if cand.show_id is None:
            raise HTTPException(
                status_code=400,
                detail="episode candidate has no show_id to attribute the event to",
            )
        event = TasteEvent(
            timestamp=datetime.now(UTC),
            content_type=ContentType.SHOW,
            show_id=cand.show_id,
            kind=kind,
            strength=strength,
            source="playback",
        )
    else:
        event = TasteEvent(
            timestamp=datetime.now(UTC),
            content_type=cand.content_type,
            archive_id=cand.archive_id,
            kind=kind,
            strength=strength,
            source="playback",
        )
    q_taste_events.insert_event(conn, event)


@router.post("/recommendations/{archive_id}/reject", status_code=204)
async def reject_recommendation(
    archive_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> Response:
    _insert_signal_event(conn, archive_id, kind=TasteEventKind.REJECTED, strength=0.3)
    # Terminal state for the candidate itself.
    q_candidates.update_status(conn, archive_id, CandidateStatus.REJECTED)
    return Response(status_code=204)


@router.post("/recommendations/{archive_id}/defer", status_code=204)
async def defer_recommendation(
    archive_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> Response:
    _insert_signal_event(conn, archive_id, kind=TasteEventKind.DEFERRED, strength=0.2)
    # Leave candidate status unchanged — defer is a soft signal.
    return Response(status_code=204)


__all__ = ["RecommendationsResponse", "router"]
