"""``archive-agent recommend`` — phase3-08 end-to-end.

Pipeline:

1. Latest ``TasteProfile`` (raise ``NoProfileError`` if missing).
2. Latest per-show ratings (ADR-013, latest-wins bulk read).
3. ``exclude_window_days`` — anything we've already surfaced recently
   drops out of the pool so the poster wall doesn't loop.
4. TF-IDF prefilter → k candidates.
5. ``LLMProvider.rank`` with the ratings dict threaded through.
6. Insert the batch into ``ranked_candidates`` for audit + exclusion
   on the next run.

Never produces the same recommendation twice within
``exclude_window_days`` regardless of provider.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from archive_agent.config import Config
from archive_agent.logging import get_logger
from archive_agent.ranking.factory import make_provider, make_provider_for_workflow
from archive_agent.ranking.provider import LLMProvider
from archive_agent.ranking.tfidf import TFIDFIndex, prefilter
from archive_agent.state.models import ContentType, RankedCandidate
from archive_agent.state.queries import (
    ranked_candidates as q_ranked,
)
from archive_agent.state.queries import (
    taste_profile_versions as q_profiles,
)
from archive_agent.taste import latest_for_all_shows

_log = get_logger("archive_agent.commands.recommend")

ProviderName = Literal["ollama", "claude", "tfidf"]


class NoProfileError(RuntimeError):
    """Raised when no ``TasteProfile`` exists — bootstrap first."""


class RecommendResult(BaseModel):
    n_requested: int
    n_returned: int
    provider: str
    items: list[RankedCandidate] = Field(default_factory=list)
    profile_version: int
    elapsed_ms: int
    batch_id: str
    prefilter_size: int
    excluded_count: int


async def recommend(
    conn: sqlite3.Connection,
    config: Config,
    *,
    n: int | None = None,
    content_types: list[ContentType] | None = None,
    force_provider: ProviderName | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    _index: TFIDFIndex | None = None,
) -> RecommendResult:
    """Produce a ranked shortlist of candidates.

    ``_index`` is a test hook — production callers let ``recommend``
    build the index lazily.
    """
    current_now = now or datetime.now(UTC)
    started = time.perf_counter()
    n_requested = n or config.recommend.default_n

    profile = q_profiles.get_latest_profile(conn)
    if profile is None:
        raise NoProfileError(
            "no taste profile yet — run `archive-agent taste bootstrap` first"
        )

    # Excludes: any archive_id recommended within the window.
    window_start = current_now - timedelta(days=config.recommend.exclude_window_days)
    excludes = q_ranked.recent_archive_ids(conn, window_start)

    ratings = latest_for_all_shows(conn)

    index = _index or TFIDFIndex.build(conn)
    shortlist = prefilter(
        index,
        conn,
        profile,
        k=config.recommend.prefilter_k,
        content_types=content_types,
        exclude_archive_ids=excludes,
    )
    candidates = [c for c, _ in shortlist]
    prefilter_size = len(candidates)

    # Provider selection: explicit override > workflow default.
    provider: LLMProvider
    provider_name: str
    if force_provider is not None:
        provider = make_provider(force_provider, config, conn=conn)
        provider_name = force_provider
    else:
        provider = make_provider_for_workflow("nightly_ranking", config, conn=conn)
        provider_name = provider.name

    if not candidates:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _log.info(
            "recommend_empty_shortlist",
            provider=provider_name,
            prefilter_size=0,
            excluded=len(excludes),
        )
        return RecommendResult(
            n_requested=n_requested,
            n_returned=0,
            provider=provider_name,
            items=[],
            profile_version=profile.version,
            elapsed_ms=elapsed_ms,
            batch_id="",
            prefilter_size=0,
            excluded_count=len(excludes),
        )

    picks = await provider.rank(profile, candidates, n=n_requested, ratings=ratings)

    batch_id = uuid.uuid4().hex
    if picks and not dry_run:
        q_ranked.insert_batch(
            conn,
            batch_id,
            picks,
            provider=provider_name,
            profile_version=profile.version,
            now=current_now,
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log.info(
        "recommend_complete",
        provider=provider_name,
        n_returned=len(picks),
        prefilter_size=prefilter_size,
        excluded=len(excludes),
        elapsed_ms=elapsed_ms,
        profile_version=profile.version,
        batch_id=batch_id if picks else "",
    )
    return RecommendResult(
        n_requested=n_requested,
        n_returned=len(picks),
        provider=provider_name,
        items=picks,
        profile_version=profile.version,
        elapsed_ms=elapsed_ms,
        batch_id=batch_id if picks and not dry_run else "",
        prefilter_size=prefilter_size,
        excluded_count=len(excludes),
    )


__all__ = ["NoProfileError", "ProviderName", "RecommendResult", "recommend"]
