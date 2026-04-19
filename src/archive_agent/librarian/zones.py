"""Media zones and their policy tiers.

The four `/media/*` subdirectories are distinct in how the agent
interacts with them. Codifying them as a ``StrEnum`` (and splitting
into USER_OWNED vs AGENT_MANAGED frozensets) means the hard guardrail
"never auto-evict from ``/media/movies``" shows up as a type check
rather than a grep-for-strings audit.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from archive_agent.config import Config

__all__ = ["AGENT_MANAGED", "USER_OWNED", "Zone", "zone_path"]


class Zone(StrEnum):
    MOVIES = "movies"  # user-owned, never auto-evicted
    TV = "tv"  # committed TV, slow eviction with grace
    RECOMMENDATIONS = "recommendations"
    TV_SAMPLER = "tv-sampler"  # hyphen matches the on-disk directory name


AGENT_MANAGED: frozenset[Zone] = frozenset({Zone.TV, Zone.RECOMMENDATIONS, Zone.TV_SAMPLER})
USER_OWNED: frozenset[Zone] = frozenset({Zone.MOVIES})


def zone_path(zone: Zone, config: Config) -> Path:
    """Return the filesystem path configured for ``zone``."""
    match zone:
        case Zone.MOVIES:
            return config.paths.media_movies
        case Zone.TV:
            return config.paths.media_tv
        case Zone.RECOMMENDATIONS:
            return config.paths.media_recommendations
        case Zone.TV_SAMPLER:
            return config.paths.media_tv_sampler
