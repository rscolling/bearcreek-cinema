"""Rank prompt — stage 2 of the two-stage pipeline.

Both ``OllamaProvider.rank`` (phase3-03) and ``ClaudeProvider.rank``
(phase3-07) use this builder so the prompt is identical across
providers. Strength / rating-window tuning constants also live here so
there's one place to adjust them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from archive_agent.state.models import (
    Candidate,
    ContentType,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)

# How far back we treat a rating as a strong prior. Older ratings are
# still read (ADR-013 latest-wins) but not injected into the prompt —
# taste drifts over 6 months of watching.
RATING_WINDOW_DAYS = 180

_THUMB_GLYPHS = {
    TasteEventKind.RATED_DOWN: "👎",
    TasteEventKind.RATED_UP: "👍",
    TasteEventKind.RATED_LOVE: "👍👍",
}


class RankItem(BaseModel):
    """One reranked pick. Instructor-compatible structured output."""

    archive_id: str
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=20, max_length=200)


class RankResponse(BaseModel):
    picks: list[RankItem]


def _rating_tag(event: TasteEvent, now: datetime) -> str | None:
    if event.timestamp < now - timedelta(days=RATING_WINDOW_DAYS):
        return None
    glyph = _THUMB_GLYPHS.get(event.kind)
    return f"[rated: {glyph}]" if glyph else None


def _render_profile(profile: TasteProfile) -> str:
    lines: list[str] = []
    if profile.summary:
        lines.append(profile.summary)
    if profile.liked_genres:
        lines.append(f"Liked genres: {', '.join(profile.liked_genres)}")
    if profile.disliked_genres:
        lines.append(f"Disliked genres: {', '.join(profile.disliked_genres)}")
    positive_eras = [e for e in profile.era_preferences if e.weight > 0]
    if positive_eras:
        eras = ", ".join(f"{e.decade}s" for e in positive_eras)
        lines.append(f"Liked eras: {eras}")
    if not lines:
        return "(no profile — household tastes unknown; pick broadly appealing items)"
    return "\n".join(lines)


def _render_candidate(
    c: Candidate,
    *,
    rating: TasteEvent | None,
    now: datetime,
) -> str:
    year = str(c.year) if c.year else "????"
    kind = c.content_type.value
    genres = ", ".join(c.genres) if c.genres else "—"
    runtime = f"{c.runtime_minutes}m" if c.runtime_minutes else "??m"
    desc = c.description.strip().replace("\n", " ")
    if len(desc) > 240:
        desc = desc[:237] + "..."
    parts = [
        f"- id={c.archive_id}",
        f"title={c.title!r}",
        f"year={year}",
        f"type={kind}",
        f"genres={genres}",
        f"runtime={runtime}",
    ]
    if rating is not None:
        tag = _rating_tag(rating, now)
        if tag is not None:
            parts.append(tag)
    line = " ".join(parts)
    if desc:
        line += f"\n  {desc}"
    return line


def build_rank_prompt(
    profile: TasteProfile,
    candidates: list[Candidate],
    *,
    n: int,
    ratings: dict[str, TasteEvent] | None = None,
    now: datetime | None = None,
) -> str:
    """Render the rank prompt.

    ``ratings`` is keyed by ``show_id``; candidates without a
    ``show_id`` or without a rating simply have no tag. Ratings older
    than ``RATING_WINDOW_DAYS`` are dropped (see ADR-013 notes).
    """
    current_now = now or datetime.now(UTC)
    ratings_map = ratings or {}

    profile_block = _render_profile(profile)

    candidate_lines: list[str] = []
    for c in candidates:
        rating = None
        if c.show_id is not None and c.content_type == ContentType.SHOW:
            rating = ratings_map.get(c.show_id)
        candidate_lines.append(_render_candidate(c, rating=rating, now=current_now))

    return f"""You are a picky, taste-matched film and TV curator for one household.
Given their taste profile and a shortlist of candidates, choose the {n} best picks.

TASTE PROFILE
-------------
{profile_block}

CANDIDATES
----------
{chr(10).join(candidate_lines)}

INSTRUCTIONS
------------
- Choose exactly {n} items from the candidates above.
- Score each pick in [0.0, 1.0] — higher is better-match.
- Write ONE concrete sentence of reasoning per pick (<=200 chars).
  Anchor in the profile (e.g., "pairs the screwball pacing you liked
  in X with a wartime ensemble"). No vague praise like
  "you'll enjoy this".
- Treat [rated: 👎] as a strong negative signal for the whole show —
  avoid similar items unless there's a clear reason.
- Treat [rated: 👍👍] as a strong positive — "more like this" gets a
  boost.
- Return JSON matching this schema exactly:
  {{"picks": [{{"archive_id": "...", "score": 0.0-1.0, "reasoning": "..."}}, ...]}}
"""


__all__ = [
    "RATING_WINDOW_DAYS",
    "RankItem",
    "RankResponse",
    "build_rank_prompt",
]
