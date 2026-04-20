"""Prompt builders for the LLMProviders.

Plain-Python builders (not Jinja) — we're rendering a few well-defined
shapes, and a dependency-free module is easier to test and unpickle
across process restarts.
"""

from archive_agent.ranking.prompts.profile import (
    SUMMARY_WORD_LIMIT,
    build_update_profile_prompt,
)
from archive_agent.ranking.prompts.rank import (
    RATING_WINDOW_DAYS,
    RankItem,
    RankResponse,
    build_rank_prompt,
)

__all__ = [
    "RATING_WINDOW_DAYS",
    "SUMMARY_WORD_LIMIT",
    "RankItem",
    "RankResponse",
    "build_rank_prompt",
    "build_update_profile_prompt",
]
