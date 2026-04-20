"""Stage 1 of the two-stage ranking pipeline — cosine prefilter.

Given a taste profile and a fitted index, return the top-k candidates
by cosine similarity. Hard filters (disliked IDs, content-type) are
applied *after* scoring so the LLM reranker (phase3-03) works over a
clean, pre-trimmed pool.
"""

from __future__ import annotations

import sqlite3
from typing import cast

from sklearn.metrics.pairwise import linear_kernel

from archive_agent.ranking.tfidf.features import profile_document
from archive_agent.ranking.tfidf.index import TFIDFIndex
from archive_agent.state.models import Candidate, ContentType, TasteProfile
from archive_agent.state.queries import candidates as q_candidates


def prefilter(
    index: TFIDFIndex,
    conn: sqlite3.Connection,
    profile: TasteProfile,
    *,
    k: int = 50,
    content_types: list[ContentType] | None = None,
    exclude_archive_ids: set[str] | None = None,
) -> list[tuple[Candidate, float]]:
    """Rank candidates by cosine similarity to the profile query.

    Disliked ``archive_ids`` and ``show_ids`` are excluded. Scores are
    already in ``[0, 1]`` because the matrix is L2-normalized.
    """
    if index.size == 0:
        return []

    query_vec = index.vectorizer.transform([profile_document(profile)])
    # linear_kernel on L2-normalized vectors == cosine similarity.
    raw_scores = linear_kernel(query_vec, index.matrix)[0]
    scores = cast("list[float]", raw_scores.tolist())

    # Hard filter: disliked IDs / shows / explicit excludes.
    disliked_archive = set(profile.disliked_archive_ids)
    disliked_shows = set(profile.disliked_show_ids)
    excludes = exclude_archive_ids or set()

    # Fetch the archive_ids we actually need so we can apply filters
    # that require per-candidate metadata without pulling all rows.
    archive_ids = index.archive_ids

    # Take a generous candidate shortlist from the scored pool so we
    # still have enough survivors after filters — rather than picking
    # top-k, dropping filtered, and potentially returning fewer.
    ranking: list[tuple[int, float]] = sorted(
        ((i, s) for i, s in enumerate(scores) if s > 0),
        key=lambda pair: pair[1],
        reverse=True,
    )

    picks: list[tuple[Candidate, float]] = []
    for row_idx, score in ranking:
        aid = archive_ids[row_idx]
        if aid in disliked_archive or aid in excludes:
            continue
        cand = q_candidates.get_by_archive_id(conn, aid)
        if cand is None:
            continue  # row in index, gone from DB — tolerate it
        if cand.show_id is not None and cand.show_id in disliked_shows:
            continue
        if content_types is not None and cand.content_type not in content_types:
            continue
        picks.append((cand, float(score)))
        if len(picks) >= k:
            break

    return picks


__all__ = ["prefilter"]
