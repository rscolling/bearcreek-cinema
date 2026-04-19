"""Archive.org discovery + (in later phases) downloading."""

from archive_agent.archive.discovery import DiscoverResult, discover
from archive_agent.archive.search import (
    ArchiveCollection,
    ArchiveSearchResult,
    search_collection,
)

__all__ = [
    "ArchiveCollection",
    "ArchiveSearchResult",
    "DiscoverResult",
    "discover",
    "search_collection",
]
