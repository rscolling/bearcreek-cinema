"""Jellyfin REST client + watch-history ingestion (phase1-04) +
scan/resolve placement (phase2-09).

``JellyfinClient`` owns HTTP I/O; higher-level helpers live in
``jellyfin.history`` and ``jellyfin.placement``. Nothing outside
this package issues HTTP calls to Jellyfin (see GUARDRAILS.md).
"""

from archive_agent.jellyfin.client import JellyfinClient
from archive_agent.jellyfin.history import (
    EpisodeWatchRecord,
    HistoryIngestResult,
    MovieWatchRecord,
    classify_movie_signal,
    fetch_episode_history,
    fetch_movie_history,
    ingest_all_history,
)
from archive_agent.jellyfin.placement import (
    LibraryMap,
    MissingLibraryError,
    resolve_libraries,
    scan_and_resolve,
    scan_zones,
)

__all__ = [
    "EpisodeWatchRecord",
    "HistoryIngestResult",
    "JellyfinClient",
    "LibraryMap",
    "MissingLibraryError",
    "MovieWatchRecord",
    "classify_movie_signal",
    "fetch_episode_history",
    "fetch_movie_history",
    "ingest_all_history",
    "resolve_libraries",
    "scan_and_resolve",
    "scan_zones",
]
