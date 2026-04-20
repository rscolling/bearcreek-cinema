"""``/recommendations/{id}/select`` and ``/shows/{id}/commit``.

Select triggers the download pipeline for one candidate; commit
bypasses the sampler-first policy for a whole show.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from archive_agent.api.dependencies import get_config, get_db
from archive_agent.commands.select import (
    CandidateNotFoundError,
    CommitResult,
    SelectResult,
    commit_show,
    select_candidate,
)
from archive_agent.config import Config

router = APIRouter()


class SelectRequest(BaseModel):
    play: bool = True


@router.post("/recommendations/{archive_id}/select")
async def select_recommendation(
    archive_id: str,
    config: Annotated[Config, Depends(get_config)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    body: SelectRequest | None = None,
) -> Response:
    req = body or SelectRequest()
    try:
        result: SelectResult = await select_candidate(conn, config, archive_id, play=req.play)
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no candidate {exc.args[0]!r}") from exc
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.detail)
    status_code = 200 if result.status == "ready" else 202
    return Response(
        content=result.model_dump_json(),
        media_type="application/json",
        status_code=status_code,
    )


@router.post("/shows/{show_id}/commit", response_model=CommitResult, status_code=202)
async def commit_show_endpoint(
    show_id: str,
    config: Annotated[Config, Depends(get_config)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> CommitResult:
    result = await commit_show(conn, config, show_id)
    if result.enqueued_downloads == 0:
        raise HTTPException(
            status_code=404, detail=f"no episode candidates for show_id={show_id!r}"
        )
    return result


__all__ = ["router"]
