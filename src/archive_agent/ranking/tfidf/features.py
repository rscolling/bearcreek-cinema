"""Render candidates and taste profiles into text for the vectorizer.

The profile becomes a *query document* that the index scores every
candidate against. Disliked signal is applied as a **post-filter** in
``prefilter`` — see ADR notes in the phase3-02 card — so this module
is deliberately positive-only.
"""

from __future__ import annotations

from archive_agent.state.models import Candidate, ContentType, TasteProfile


def _runtime_bucket(minutes: int | None) -> str:
    """Coarse runtime bucket so the model groups short films with shorts
    and feature-length movies with features, rather than scattering
    every minute into its own dimension."""
    if minutes is None:
        return "runtime_unknown"
    if minutes < 40:
        return "runtime_short"
    if minutes < 90:
        return "runtime_medium"
    if minutes < 150:
        return "runtime_feature"
    return "runtime_long"


def _decade_token(year: int | None) -> str:
    if year is None:
        return "decade_unknown"
    decade = (year // 10) * 10
    return f"decade_{decade}s"


def _type_token(content_type: ContentType) -> str:
    return f"type_{content_type.value}"


def candidate_document(c: Candidate) -> str:
    """Deterministic text rendering of a candidate.

    The same candidate always produces the exact same string, so the
    TF-IDF matrix build is reproducible across restarts.
    """
    parts: list[str] = [c.title, _type_token(c.content_type), _decade_token(c.year)]
    parts.append(_runtime_bucket(c.runtime_minutes))
    parts.extend(f"genre_{g.lower().replace(' ', '_')}" for g in c.genres)
    if c.description:
        parts.append(c.description)
    return " ".join(parts)


def profile_document(profile: TasteProfile) -> str:
    """Render a taste profile into a query document.

    Liked genres are doubled so a profile that loves noir gets more
    weight on noir than a profile that merely mentions it in prose.
    Disliked content is NOT negated here — instead, ``prefilter``
    post-filters it out. Summary prose is included as-is; the LLM
    reranker gets to reason over it, but the TF-IDF layer extracts
    incidental keyword signal from it.
    """
    parts: list[str] = []
    for genre in profile.liked_genres:
        token = f"genre_{genre.lower().replace(' ', '_')}"
        parts.append(token)
        parts.append(token)  # duplicate so it dominates in TF weighting
    for era in profile.era_preferences:
        if era.weight > 0:
            parts.append(f"decade_{era.decade}s")
    if profile.summary:
        parts.append(profile.summary)
    return " ".join(parts) if parts else "content"


__all__ = ["candidate_document", "profile_document"]
