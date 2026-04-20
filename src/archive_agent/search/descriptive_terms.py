"""Curated term list that flags a query as descriptive intent.

The list is intentionally broad and human-curated rather than learned
from data. Hit rate > precision: if one of these words appears, we'd
rather route to the descriptive pipeline (TF-IDF + ranker) than to
FTS title-match, even if the actual intent is ambiguous.

Keep entries lowercase. Matching is substring-free — we split the
query into tokens and check membership, so "noir" matches "noir" but
not "noirish" (intentional; tokenization handles plurals separately).
"""

from __future__ import annotations

DESCRIPTIVE_TERMS: frozenset[str] = frozenset(
    # Genres + film-adjacent labels
    {
        "noir", "neo-noir", "comedy", "comedic", "drama", "dramatic",
        "horror", "scary", "mystery", "thriller", "sci-fi", "science",
        "fiction", "western", "romance", "romantic", "screwball",
        "documentary", "animation", "animated", "silent", "musical",
        "action", "adventure", "war", "biopic", "fantasy", "sitcom",
        "procedural", "anthology",
    }
    # Quality / length
    | {
        "short", "shorts", "long", "quick", "classic", "classics",
        "modern", "feature", "feature-length", "epic", "miniseries",
    }
    # Mood
    | {
        "funny", "hilarious", "heartwarming", "dark", "moody",
        "atmospheric", "cozy", "bleak", "uplifting", "campy",
        "disturbing", "offbeat", "weird",
    }
    # Era words
    | {
        "forties", "fifties", "sixties", "seventies", "eighties",
        "nineties", "pre-code", "golden-age", "new-hollywood",
    }
    # Negations
    | {"anything", "but", "not", "except", "avoid"}
    # Prompt markers
    | {"something", "anything"}
)


__all__ = ["DESCRIPTIVE_TERMS"]
