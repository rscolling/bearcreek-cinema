"""TMDb metadata enrichment for Archive.org candidates (phase2-02).

Only ``metadata.tmdb`` speaks HTTP to TMDb. Callers use
``enrich_candidate`` / ``enrich_new_candidates`` which wrap the
client with the non-overwrite + cache policy.
"""

from archive_agent.metadata.enrich import (
    EnrichResult,
    enrich_candidate,
    enrich_new_candidates,
)
from archive_agent.metadata.models import (
    TmdbConfiguration,
    TmdbGenre,
    TmdbMovie,
    TmdbShow,
)
from archive_agent.metadata.tmdb import TmdbClient, TmdbError

__all__ = [
    "EnrichResult",
    "TmdbClient",
    "TmdbConfiguration",
    "TmdbError",
    "TmdbGenre",
    "TmdbMovie",
    "TmdbShow",
    "enrich_candidate",
    "enrich_new_candidates",
]
