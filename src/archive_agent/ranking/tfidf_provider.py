"""TF-IDF fallback LLMProvider — no LLM at all (ADR-002).

Used when Ollama is down (via ``FallbackProvider``) or as a deliberate
cheap path selected in ``[llm.workflows]``. Routing through
``audit_llm_call`` (``provider="tfidf"``) lets us compare fallback vs
LLM usage from ``llm_calls`` later.

Ranking uses a lazily-built ``TFIDFIndex`` + cosine scoring with coarse
adjustments for ADR-013 explicit show ratings:

- ``RATED_LOVE`` / ``RATED_UP`` on a show → +bump for any candidate
  sharing its ``show_id``.
- ``RATED_DOWN`` on a show → heavy penalty (effectively excluded).

Templated reasoning is intentionally ugly so users notice when we're in
fallback mode.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

from archive_agent.ranking.audit import audit_llm_call
from archive_agent.ranking.provider import HealthStatus
from archive_agent.ranking.tfidf.features import profile_document
from archive_agent.ranking.tfidf.index import TFIDFIndex
from archive_agent.state.models import (
    Candidate,
    ContentType,
    EraPreference,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)

__all__ = ["TFIDFProvider"]

_MODEL_NAME = "tfidf-v1"

# Coarse rating adjustments. The LLM can reason about subtlety; TF-IDF
# uses hammers. Tune only if fallback starts getting used in anger.
_RATING_LOVE_BOOST = 0.3
_RATING_UP_BOOST = 0.15
_RATING_DOWN_PENALTY = 0.5

# Stopwords for parse_search — enough to strip the obvious filler,
# not trying to match scikit-learn's list.
_SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "on",
        "in",
        "at",
        "with",
        "for",
        "to",
        "from",
        "by",
        "like",
        "me",
        "i",
        "something",
        "anything",
        "find",
        "please",
    }
)

_DECADE_RE = re.compile(r"\b(?:19|20)?([0-9])0s\b", re.IGNORECASE)
_ERA_RANGE_RE = re.compile(r"\b(19|20)(\d{2})\s*[-\u2013]\s*(19|20)?(\d{2})\b")


class TFIDFProvider:
    name = "tfidf"

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        index: TFIDFIndex | None = None,
    ) -> None:
        self._conn = conn
        self._index = index

    # --- index caching --------------------------------------------------

    def _get_index(self) -> TFIDFIndex | None:
        """Return a fitted index, building it from ``self._conn`` on first
        use. Returns ``None`` when no conn is attached (e.g., tests)."""
        if self._index is not None:
            return self._index
        if self._conn is None:
            return None
        self._index = TFIDFIndex.build(self._conn)
        return self._index

    # --- LLMProvider -----------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """``ok`` when the candidate corpus has rows, ``degraded`` when empty."""
        async with audit_llm_call("tfidf", _MODEL_NAME, "health_check", conn=self._conn) as ctx:
            index = self._get_index()
            if index is None or index.size == 0:
                return HealthStatus(
                    status="degraded",
                    detail="no candidates in corpus — ranker will return empty",
                    model=_MODEL_NAME,
                    latency_ms=ctx.latency_ms,
                )
            return HealthStatus(
                status="ok",
                detail=f"corpus size: {index.size}",
                model=_MODEL_NAME,
                latency_ms=ctx.latency_ms,
            )

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]:
        if not candidates:
            return []

        ratings_map = ratings or {}
        index = self._get_index()

        async with audit_llm_call("tfidf", _MODEL_NAME, "rank", conn=self._conn):
            scores = _cosine_scores(profile, candidates, index)
            adjusted = _apply_rating_adjustments(candidates, scores, ratings_map)

            order = sorted(
                range(len(candidates)),
                key=lambda i: adjusted[i],
                reverse=True,
            )
            # Drop anything that the rating penalty knocked below zero —
            # "effectively excluded" per the ADR-013 semantics.
            filtered = [i for i in order if adjusted[i] > 0.0]
            picks: list[RankedCandidate] = []
            for rank_ix, cand_ix in enumerate(filtered[:n], start=1):
                cand = candidates[cand_ix]
                picks.append(
                    RankedCandidate(
                        candidate=cand,
                        score=max(0.0, min(1.0, adjusted[cand_ix])),
                        reasoning=_templated_reason(cand, profile, ratings_map),
                        rank=rank_ix,
                    )
                )
            return picks

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        """Deterministic merge. Version bumps, summary is templated."""
        async with audit_llm_call("tfidf", _MODEL_NAME, "update_profile", conn=self._conn):
            return _deterministic_update(current, events, conn=self._conn)

    async def parse_search(self, query: str) -> SearchFilter:
        """Coarse keyword extraction — no NL, no LLM."""
        async with audit_llm_call("tfidf", _MODEL_NAME, "parse_search", conn=self._conn):
            return _parse_search_keywords(query)


# --- rank helpers -----------------------------------------------------------


def _cosine_scores(
    profile: TasteProfile,
    candidates: list[Candidate],
    index: TFIDFIndex | None,
) -> list[float]:
    """Score each candidate against the profile. Falls back to uniform
    scoring when the index isn't available (e.g., conn-less test setup)."""
    if index is None or index.size == 0:
        return [0.5 for _ in candidates]
    from sklearn.metrics.pairwise import linear_kernel

    query_vec = index.vectorizer.transform([profile_document(profile)])
    # Score only the candidates we were handed — they may be a subset of
    # the whole corpus (prefilter output).
    scores: list[float] = []
    for cand in candidates:
        row = index.row_for(cand.archive_id)
        if row is None:
            scores.append(0.0)
            continue
        raw = float(linear_kernel(query_vec, index.matrix[row]).item())
        scores.append(max(0.0, min(1.0, raw)))
    return scores


def _apply_rating_adjustments(
    candidates: list[Candidate],
    scores: list[float],
    ratings: dict[str, TasteEvent],
) -> list[float]:
    """Shift scores by explicit-rating prior (ADR-013)."""
    out = list(scores)
    for i, cand in enumerate(candidates):
        if cand.show_id is None:
            continue
        rating = ratings.get(cand.show_id)
        if rating is None:
            continue
        if rating.kind == TasteEventKind.RATED_LOVE:
            out[i] += _RATING_LOVE_BOOST
        elif rating.kind == TasteEventKind.RATED_UP:
            out[i] += _RATING_UP_BOOST
        elif rating.kind == TasteEventKind.RATED_DOWN:
            out[i] -= _RATING_DOWN_PENALTY
    return out


def _templated_reason(
    cand: Candidate,
    profile: TasteProfile,
    ratings: dict[str, TasteEvent],
) -> str:
    """Short, obvious, clearly-not-LLM reasoning string."""
    shared_genres = [g for g in cand.genres if g in profile.liked_genres]
    if cand.show_id is not None:
        rating = ratings.get(cand.show_id)
        if rating is not None and rating.kind == TasteEventKind.RATED_LOVE:
            return f"TF-IDF: double-thumbs-up show match ({cand.title})."
        if rating is not None and rating.kind == TasteEventKind.RATED_UP:
            return f"TF-IDF: thumbs-up show match ({cand.title})."
    if shared_genres:
        joined = ", ".join(shared_genres[:2])
        return f"TF-IDF: shared genres ({joined})."
    return "TF-IDF: similarity match."


# --- update_profile helpers -------------------------------------------------


def _deterministic_update(
    current: TasteProfile,
    events: list[TasteEvent],
    *,
    conn: sqlite3.Connection | None,
) -> TasteProfile:
    positive_archive: set[str] = set(current.liked_archive_ids)
    negative_archive: set[str] = set(current.disliked_archive_ids)
    positive_show: set[str] = set(current.liked_show_ids)
    negative_show: set[str] = set(current.disliked_show_ids)

    finished_runtimes: list[int] = []
    decade_tally: dict[int, int] = {}
    liked_genre_tally: dict[str, int] = {}
    disliked_genre_tally: dict[str, int] = {}

    for event in events:
        positive = event.kind in {
            TasteEventKind.FINISHED,
            TasteEventKind.REWATCHED,
            TasteEventKind.APPROVED,
            TasteEventKind.BINGE_POSITIVE,
            TasteEventKind.RATED_UP,
            TasteEventKind.RATED_LOVE,
        }
        negative = event.kind in {
            TasteEventKind.ABANDONED,
            TasteEventKind.REJECTED,
            TasteEventKind.BINGE_NEGATIVE,
            TasteEventKind.RATED_DOWN,
        }
        if event.archive_id is not None:
            if positive:
                positive_archive.add(event.archive_id)
                negative_archive.discard(event.archive_id)
            elif negative:
                negative_archive.add(event.archive_id)
                positive_archive.discard(event.archive_id)
        if event.show_id is not None:
            if positive:
                positive_show.add(event.show_id)
                negative_show.discard(event.show_id)
            elif negative:
                negative_show.add(event.show_id)
                positive_show.discard(event.show_id)

        cand = _lookup_candidate(conn, event)
        if cand is not None:
            if event.kind == TasteEventKind.FINISHED and cand.runtime_minutes:
                finished_runtimes.append(cand.runtime_minutes)
            if event.kind in {TasteEventKind.FINISHED, TasteEventKind.REWATCHED} and cand.year:
                decade = (cand.year // 10) * 10
                decade_tally[decade] = decade_tally.get(decade, 0) + 1
            target_tally = (
                liked_genre_tally if positive else (disliked_genre_tally if negative else None)
            )
            if target_tally is not None:
                for g in cand.genres:
                    target_tally[g] = target_tally.get(g, 0) + 1

    liked_genres = _top_genres(liked_genre_tally, current.liked_genres)
    disliked_genres = _top_genres(disliked_genre_tally, current.disliked_genres)

    era_preferences = _decade_prefs(decade_tally) or list(current.era_preferences)

    runtime_tolerance = (
        _percentile(finished_runtimes, 95)
        if finished_runtimes
        else current.runtime_tolerance_minutes
    )

    summary = _templated_summary(liked_genres, disliked_genres, era_preferences)

    return current.model_copy(
        update={
            "version": current.version + 1,
            "updated_at": datetime.now(UTC),
            "liked_genres": liked_genres,
            "disliked_genres": disliked_genres,
            "era_preferences": era_preferences,
            "runtime_tolerance_minutes": runtime_tolerance,
            "liked_archive_ids": sorted(positive_archive),
            "disliked_archive_ids": sorted(negative_archive),
            "liked_show_ids": sorted(positive_show),
            "disliked_show_ids": sorted(negative_show),
            "summary": summary,
        }
    )


def _lookup_candidate(conn: sqlite3.Connection | None, event: TasteEvent) -> Candidate | None:
    if conn is None:
        return None
    from archive_agent.state.queries import candidates as q_candidates

    if event.archive_id is not None:
        return q_candidates.get_by_archive_id(conn, event.archive_id)
    if event.show_id is not None:
        row = conn.execute(
            "SELECT archive_id FROM candidates WHERE show_id = ? "
            "AND content_type = 'episode' LIMIT 1",
            (event.show_id,),
        ).fetchone()
        if row is not None:
            return q_candidates.get_by_archive_id(conn, row["archive_id"])
    return None


def _top_genres(tally: dict[str, int], fallback: list[str]) -> list[str]:
    if not tally:
        return list(fallback)
    return [g for g, _ in sorted(tally.items(), key=lambda p: (-p[1], p[0]))][:8]


def _decade_prefs(tally: dict[int, int]) -> list[EraPreference]:
    if not tally:
        return []
    max_count = max(tally.values())
    prefs: list[EraPreference] = []
    for decade, count in sorted(tally.items()):
        # Normalize to [-1, 1]; we only generate positive weights here.
        weight = min(1.0, count / max_count)
        prefs.append(EraPreference(decade=decade, weight=round(weight, 2)))
    return prefs


def _percentile(values: list[int], p: int) -> int:
    if not values:
        return 0
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(len(values) * p / 100)))
    return values[k]


def _templated_summary(
    liked_genres: list[str],
    disliked_genres: list[str],
    era_preferences: list[EraPreference],
) -> str:
    parts = ["TF-IDF profile (templated, no LLM)."]
    if liked_genres:
        parts.append(f"Likes: {', '.join(liked_genres[:4])}.")
    if disliked_genres:
        parts.append(f"Avoids: {', '.join(disliked_genres[:2])}.")
    eras = [e for e in era_preferences if e.weight > 0]
    if eras:
        decades = ", ".join(f"{e.decade}s" for e in sorted(eras, key=lambda e: -e.weight)[:3])
        parts.append(f"Eras: {decades}.")
    if len(parts) == 1:
        parts.append("No strong signal yet.")
    return " ".join(parts)


# --- parse_search helpers ---------------------------------------------------


def _parse_search_keywords(query: str) -> SearchFilter:
    tokens = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
    content_types: list[ContentType] | None = None
    if any(t in {"movie", "movies", "film", "films"} for t in tokens):
        content_types = [ContentType.MOVIE]
    elif any(t in {"show", "shows", "series", "tv"} for t in tokens):
        content_types = [ContentType.SHOW]

    max_runtime: int | None = None
    if "short" in tokens:
        max_runtime = 40
    elif any(t in {"feature", "feature-length"} for t in tokens):
        max_runtime = 150

    era: tuple[int, int] | None = _detect_era(query)

    keywords = [
        t
        for t in tokens
        if t not in _SEARCH_STOPWORDS
        and t not in {"movie", "movies", "film", "films", "show", "shows", "series", "tv"}
        and not _DECADE_RE.fullmatch(t)
        and not t.isdigit()
    ]

    return SearchFilter(
        content_types=content_types,
        max_runtime_minutes=max_runtime,
        era=era,
        keywords=keywords,
    )


def _detect_era(query: str) -> tuple[int, int] | None:
    """Match '40s' / '1940s' / '1940-1959'. Ambiguous two-digit decades
    default to 20th century (40s → 1940s)."""
    match = _ERA_RANGE_RE.search(query)
    if match:
        start_cent, start_dec, end_cent_opt, end_dec = match.groups()
        start = int(start_cent + start_dec)
        end_cent = end_cent_opt or start_cent
        end = int(end_cent + end_dec)
        if start <= end:
            return (start, end)
    match = _DECADE_RE.search(query)
    if match:
        decade_digit = int(match.group(1))
        # "40s" → 1940s (common case); "00s" → 2000s.
        start = 2000 if decade_digit == 0 else 1900 + decade_digit * 10
        return (start, start + 9)
    return None
