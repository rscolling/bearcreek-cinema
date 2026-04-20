"""Profile prompt — used by both bootstrap (phase3-04) and
incremental update (phase3-05). The LLM's job is to return a
``TasteProfile`` with prose summary + structured lists.

Keep the prompt short: summary prose costs tokens on both
directions, and a 300-word summary consistently beats a 1500-word
one for downstream ranking quality.
"""

from __future__ import annotations

from datetime import datetime

from archive_agent.state.models import (
    Candidate,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)

SUMMARY_WORD_LIMIT = 300
_RATING_KIND_LABEL = {
    TasteEventKind.RATED_DOWN: "thumbs-down (no more of these)",
    TasteEventKind.RATED_UP: "thumbs-up (more like this)",
    TasteEventKind.RATED_LOVE: "double thumbs-up (obsessively)",
}


def _render_current_profile(profile: TasteProfile) -> str:
    if profile.version == 0 and not profile.summary:
        return "(none — this is the first profile)"
    lines = [f"version: {profile.version}"]
    if profile.summary:
        lines.append(f"summary: {profile.summary}")
    if profile.liked_genres:
        lines.append(f"liked_genres: {', '.join(profile.liked_genres)}")
    if profile.disliked_genres:
        lines.append(f"disliked_genres: {', '.join(profile.disliked_genres)}")
    positive_eras = [e for e in profile.era_preferences if e.weight > 0]
    if positive_eras:
        lines.append("liked_eras: " + ", ".join(f"{e.decade}s" for e in positive_eras))
    if profile.liked_archive_ids:
        lines.append(f"liked_ids: {len(profile.liked_archive_ids)} archive items")
    if profile.liked_show_ids:
        lines.append(f"liked_shows: {len(profile.liked_show_ids)} shows")
    return "\n".join(lines)


def _event_label(event: TasteEvent) -> str:
    return event.kind.value


def _title_for_event(event: TasteEvent, candidates_by_id: dict[str, Candidate]) -> str:
    if event.archive_id is not None:
        cand = candidates_by_id.get(event.archive_id)
        if cand is not None:
            year = f" ({cand.year})" if cand.year else ""
            return f"{cand.title}{year}"
        # Jellyfin-history phantoms (archive_id starts with "jellyfin:")
        # won't be in the candidates table; fall back to the id.
        return event.archive_id
    if event.show_id is not None:
        cand = candidates_by_id.get(event.show_id)
        if cand is not None:
            return cand.title
        return f"show:{event.show_id}"
    return "(unknown)"


def _render_events(events: list[TasteEvent], candidates_by_id: dict[str, Candidate]) -> str:
    if not events:
        return "(no events)"
    # Group by kind so the prompt is compact even for large histories.
    by_kind: dict[str, list[str]] = {}
    for event in events:
        kind = _event_label(event)
        title = _title_for_event(event, candidates_by_id)
        genres = ""
        ref_id = event.archive_id or event.show_id
        if ref_id is not None:
            cand = candidates_by_id.get(ref_id)
            if cand and cand.genres:
                genres = f" [{', '.join(cand.genres[:3])}]"
        by_kind.setdefault(kind, []).append(f"{title}{genres}")
    lines: list[str] = []
    for kind in sorted(by_kind):
        titles = by_kind[kind]
        sample = ", ".join(titles[:12])
        suffix = f" ... +{len(titles) - 12} more" if len(titles) > 12 else ""
        lines.append(f"- {kind} ({len(titles)}): {sample}{suffix}")
    return "\n".join(lines)


def _render_ratings(ratings: dict[str, TasteEvent], candidates_by_id: dict[str, Candidate]) -> str:
    if not ratings:
        return "(no explicit ratings)"
    lines = []
    for event in ratings.values():
        title = _title_for_event(event, candidates_by_id)
        label = _RATING_KIND_LABEL.get(event.kind, event.kind.value)
        lines.append(f"- {title} — {label}")
    return "\n".join(sorted(lines))


def build_update_profile_prompt(
    current: TasteProfile,
    events: list[TasteEvent],
    *,
    ratings: dict[str, TasteEvent] | None = None,
    candidates_by_id: dict[str, Candidate] | None = None,
    now: datetime | None = None,
) -> str:
    """Render the update-profile prompt.

    ``events`` is everything new since the last profile (for bootstrap
    this is the full synthesized history). ``ratings`` is the latest-
    wins per-show thumb map — treat these as strong priors that dominate
    the corresponding implicit signals (ADR-013). ``candidates_by_id``
    maps archive_id and show_id to Candidate rows for title/genre lookup;
    pass an empty dict if no enrichment is possible.
    """
    now = now  # reserved for future use
    candidates_by_id = candidates_by_id or {}
    ratings_map = ratings or {}

    current_block = _render_current_profile(current)
    events_block = _render_events(events, candidates_by_id)
    ratings_block = _render_ratings(ratings_map, candidates_by_id)

    return f"""You are curating a household's film + TV taste profile.
Given the current profile and new signal, produce an updated TasteProfile
as JSON.

CURRENT PROFILE
---------------
{current_block}

NEW SIGNAL
----------
{events_block}

EXPLICIT SHOW RATINGS (latest-wins; strong priors)
---------------------------------------------------
{ratings_block}

INSTRUCTIONS
------------
- Write a crisp, human, <= {SUMMARY_WORD_LIMIT}-word ``summary`` —
  content-agnostic across film and TV. Describe WHY they like what
  they like, not just a list of titles.
- Populate ``liked_genres`` / ``disliked_genres`` with 3-8 items each
  based on the signal.
- ``era_preferences`` as list of {{"decade": 1940, "weight": 0.7}}
  entries, weight in [-1, 1]. Only include decades with evidence.
- Preserve IDs: any archive_id / show_id referenced in positive
  signals (finished/rewatched/binge_positive/rated_up/rated_love)
  must appear in ``liked_archive_ids`` or ``liked_show_ids``. Same
  for negative signals → disliked lists.
- Thumbs-down shows are always disliked; thumbs-love shows are
  always liked, regardless of other signals.
- Return JSON matching this schema (do not omit fields):
  {{"version": <int>, "updated_at": "<ISO-8601>",
    "summary": "...",
    "liked_genres": [...], "disliked_genres": [...],
    "era_preferences": [...],
    "runtime_tolerance_minutes": <int>,
    "liked_archive_ids": [...], "liked_show_ids": [...],
    "disliked_archive_ids": [...], "disliked_show_ids": [...]}}
"""


__all__ = ["SUMMARY_WORD_LIMIT", "build_update_profile_prompt"]
