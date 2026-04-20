"""TF-IDF prefilter — stage 1 of the two-stage ranking pipeline.

See ADR-012 (SQLite FTS5 + in-memory TF-IDF, no vector DB).
"""

from archive_agent.ranking.tfidf.features import candidate_document, profile_document
from archive_agent.ranking.tfidf.index import TFIDFIndex, TFIDFPickleError, load_or_build
from archive_agent.ranking.tfidf.prefilter import prefilter

__all__ = [
    "TFIDFIndex",
    "TFIDFPickleError",
    "candidate_document",
    "load_or_build",
    "prefilter",
    "profile_document",
]
