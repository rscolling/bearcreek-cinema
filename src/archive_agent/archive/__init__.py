"""Archive.org discovery (phase2-01) + downloading (phase2-04)."""

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

__all__ = [
    "ArchiveCollection",
    "ArchiveSearchResult",
    "DiscoverResult",
    "DownloadRequest",
    "DownloadResult",
    "discover",
    "download_one",
    "pick_format",
    "search_collection",
]
