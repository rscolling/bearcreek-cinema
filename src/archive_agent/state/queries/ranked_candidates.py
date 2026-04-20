"""CRUD for the ``ranked_candidates`` audit log (phase3-08)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from archive_agent.state.models import RankedCandidate
from archive_agent.state.queries import candidates as q_candidates


def insert_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    items: list[RankedCandidate],
    *,
    provider: str,
    profile_version: int,
    now: datetime | None = None,
) -> None:
    """Append every pick from a ``recommend()`` run, tagged by ``batch_id``."""
    ts = (now or datetime.now(UTC)).isoformat()
    rows = [
        (
            batch_id,
            r.candidate.archive_id,
            r.rank,
            r.score,
            r.reasoning,
            provider,
            profile_version,
            ts,
        )
        for r in items
    ]
    conn.executemany(
        """
        INSERT INTO ranked_candidates (
            batch_id, archive_id, rank, score, reasoning,
            provider, profile_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def latest_batch(conn: sqlite3.Connection) -> list[RankedCandidate]:
    """Return the most recent batch as a list of ``RankedCandidate``.

    Joins back to ``candidates`` to rehydrate the full candidate row.
    Entries whose candidate has since been deleted are silently dropped.
    """
    row = conn.execute(
        "SELECT batch_id FROM ranked_candidates ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    batch_id = row["batch_id"]

    rows = conn.execute(
        "SELECT archive_id, rank, score, reasoning "
        "FROM ranked_candidates WHERE batch_id = ? ORDER BY rank",
        (batch_id,),
    ).fetchall()

    picks: list[RankedCandidate] = []
    for r in rows:
        cand = q_candidates.get_by_archive_id(conn, r["archive_id"])
        if cand is None:
            continue
        picks.append(
            RankedCandidate(
                candidate=cand,
                score=float(r["score"]),
                reasoning=r["reasoning"],
                rank=int(r["rank"]),
            )
        )
    return picks


def recent_archive_ids(conn: sqlite3.Connection, since: datetime) -> set[str]:
    """Archive IDs recommended on or after ``since``. Used by the
    ``exclude_window_days`` gate so fresh batches don't recycle picks.
    """
    rows = conn.execute(
        "SELECT DISTINCT archive_id FROM ranked_candidates WHERE created_at >= ?",
        (since.isoformat(),),
    ).fetchall()
    return {r["archive_id"] for r in rows}


__all__ = ["insert_batch", "latest_batch", "recent_archive_ids"]
