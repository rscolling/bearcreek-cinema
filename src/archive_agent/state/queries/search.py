"""Catalog search via the FTS5 virtual table (migration 006).

bm25 scores come out of SQLite as *negative* numbers where lower is
better. We flip them into a ``[0.0, 1.0]`` positive-is-better scale
before returning — the API layer never sees the raw bm25.

Trigram tokenizer handles typos / missing letters without extra
post-processing. Content-type filtering is a JOIN against the parent
``candidates`` table — FTS5 doesn't carry ``content_type`` itself so a
typo-tolerant "noir" search doesn't accidentally score MOVIE and SHOW
differently.
"""

from __future__ import annotations

import math
import sqlite3

from archive_agent.state.models import Candidate, ContentType
from archive_agent.state.queries import candidates as q_candidates


def _normalize_bm25(raw: float) -> float:
    """SQLite bm25 is negative, open-ended; fold to ``[0, 1]`` higher-is-better.

    More-negative raw means a better match, so its absolute value is a
    monotonically-increasing relevance signal. ``|raw| / (1 + |raw|)``
    gives us a smooth [0, 1) mapping with better matches closer to 1.
    """
    if not math.isfinite(raw):
        return 0.0
    magnitude = abs(raw)
    return round(magnitude / (1.0 + magnitude), 4)


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    content_type: ContentType | None = None,
) -> list[tuple[Candidate, float]]:
    """Return best-first ``(candidate, score)`` pairs for the FTS query.

    Empty queries return ``[]``. Scores are normalized so higher is
    better.
    """
    if not query.strip():
        return []

    sql = """
        SELECT c.*, bm25(candidates_fts) AS raw_score
          FROM candidates_fts
          JOIN candidates c ON c.archive_id = candidates_fts.archive_id
         WHERE candidates_fts MATCH ?
    """
    params: list[object] = [query]
    if content_type is not None:
        sql += " AND c.content_type = ?"
        params.append(content_type.value)
    sql += " ORDER BY raw_score LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    out: list[tuple[Candidate, float]] = []
    for row in rows:
        cand = q_candidates.get_by_archive_id(conn, row["archive_id"])
        if cand is None:
            continue
        out.append((cand, _normalize_bm25(float(row["raw_score"]))))
    return out


def fts_autocomplete(
    conn: sqlite3.Connection,
    prefix: str,
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Prefix type-ahead over titles.

    FTS5 supports column-scoped prefix queries with ``column:prefix*``.
    We only match against the ``title`` column to avoid surfacing
    candidates whose prefix hit lives in a long description.
    """
    prefix = prefix.strip()
    if not prefix:
        return []
    # Escape quotes to survive the MATCH syntax.
    safe = prefix.replace('"', '""')
    match = f'title:"{safe}"*'
    rows = conn.execute(
        """
        SELECT c.archive_id, c.title
          FROM candidates_fts
          JOIN candidates c ON c.archive_id = candidates_fts.archive_id
         WHERE candidates_fts MATCH ?
         ORDER BY bm25(candidates_fts)
         LIMIT ?
        """,
        (match, limit),
    ).fetchall()
    return [{"archive_id": r["archive_id"], "title": r["title"]} for r in rows]


__all__ = ["fts_autocomplete", "fts_search"]
