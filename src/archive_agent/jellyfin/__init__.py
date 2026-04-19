"""Jellyfin REST client + watch-history ingestion (phase1-04).

``JellyfinClient`` owns HTTP I/O; higher-level helpers live in
``jellyfin.history``. Nothing outside this package issues HTTP calls to
Jellyfin (see GUARDRAILS.md).
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

__all__ = [
    "EpisodeWatchRecord",
    "HistoryIngestResult",
    "JellyfinClient",
    "MovieWatchRecord",
    "classify_movie_signal",
    "fetch_episode_history",
    "fetch_movie_history",
    "ingest_all_history",
]
