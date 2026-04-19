"""Archive.org discovery (phase2-01) + downloading (phase2-04) +
TV grouping (phase2-03)."""

from archive_agent.archive.discovery import DiscoverResult, discover
from archive_agent.archive.downloader import (
    DownloadRequest,
    DownloadResult,
    download_one,
    pick_format,
)
from archive_agent.archive.search import (
    ArchiveCollection,
    ArchiveSearchResult,
    search_collection,
)
from archive_agent.archive.tv_grouping import (
    GroupingMatch,
    GroupingResult,
    classify_episode,
    group_unassigned_episodes,
    parse_episode_marker,
)

__all__ = [
    "ArchiveCollection",
    "ArchiveSearchResult",
    "DiscoverResult",
    "DownloadRequest",
    "DownloadResult",
    "GroupingMatch",
    "GroupingResult",
    "classify_episode",
    "discover",
    "download_one",
    "group_unassigned_episodes",
    "parse_episode_marker",
    "pick_format",
    "search_collection",
]
