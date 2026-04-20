"""Query routing + search-intent dispatch.

Sits between the HTTP layer (``api/routes/search.py``) and the
retrieval primitives (FTS5, TF-IDF, the ranker). Classifies a raw
user query, extracts structured facets where it can, and hands the
endpoint a concrete plan — ``TITLE`` dispatches to FTS, ``DESCRIPTIVE``
to TF-IDF + taste profile, and so on.
"""

from archive_agent.search.normalize import normalize_query
from archive_agent.search.router import (
    QueryIntent,
    QueryRouteResult,
    route_query,
)

__all__ = [
    "QueryIntent",
    "QueryRouteResult",
    "normalize_query",
    "route_query",
]
