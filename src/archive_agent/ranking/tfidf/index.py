"""In-memory TF-IDF index over ``candidates`` (ADR-012).

Design notes:

- Fitted ``TfidfVectorizer`` + sparse L2-normalized document matrix.
  Linear-kernel scoring (= cosine for L2-normalized vectors) is fast
  enough at O(10^4) candidates that rebuilds are cheap.
- ``save`` / ``load`` pickle the whole thing so daemon restarts are
  warm. Pickles are tagged with the sklearn version that wrote them;
  a mismatch on load raises a clear error instead of producing silent
  garbage.
"""

from __future__ import annotations

import pickle
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sklearn
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from archive_agent.ranking.tfidf.features import candidate_document
from archive_agent.state.models import Candidate
from archive_agent.state.queries import candidates as q_candidates

_PICKLE_MAGIC = b"ARCHIVE_AGENT_TFIDF_V1"


class TFIDFPickleError(RuntimeError):
    """Raised when a saved index can't be loaded (version / format drift)."""


@dataclass
class _SavedIndex:
    magic: bytes
    sklearn_version: str
    vectorizer: TfidfVectorizer
    matrix: csr_matrix
    archive_ids: list[str]


class TFIDFIndex:
    """Fitted TF-IDF vector space over the candidates table."""

    def __init__(
        self,
        vectorizer: TfidfVectorizer,
        matrix: csr_matrix,
        archive_ids: list[str],
    ) -> None:
        self.vectorizer = vectorizer
        self.matrix = matrix
        self.archive_ids = archive_ids
        self._id_to_row: dict[str, int] = {aid: i for i, aid in enumerate(archive_ids)}

    @property
    def size(self) -> int:
        return len(self.archive_ids)

    def row_for(self, archive_id: str) -> int | None:
        """Row index of ``archive_id`` in ``matrix``, or None if absent."""
        return self._id_to_row.get(archive_id)

    # --- construction -------------------------------------------------

    @classmethod
    def build(cls, conn: sqlite3.Connection) -> TFIDFIndex:
        """Fit a fresh vectorizer against every candidate in the DB."""
        candidates = q_candidates.list_all(conn)
        return cls._fit(candidates)

    @classmethod
    def _fit(cls, candidates: list[Candidate]) -> TFIDFIndex:
        archive_ids = [c.archive_id for c in candidates]
        docs = [candidate_document(c) for c in candidates]
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            min_df=1 if len(docs) < 10 else 2,
            stop_words="english",
            sublinear_tf=True,
            norm="l2",
        )
        if not docs:
            # Empty corpus: fit against a non-empty sentinel token so the
            # vectorizer is pickleable, but expose a (0, N) matrix.
            # Callers check ``size == 0`` and short-circuit before hitting
            # ``transform`` in the hot path.
            vectorizer.fit(["corpus_sentinel"])
            empty_matrix: csr_matrix = csr_matrix((0, len(vectorizer.vocabulary_)))
            return cls(vectorizer, empty_matrix, [])
        matrix = vectorizer.fit_transform(docs)
        # TfidfVectorizer(norm='l2') already L2-normalizes, so linear_kernel
        # will give cosine similarity directly.
        return cls(vectorizer, matrix, archive_ids)

    def refresh(self, conn: sqlite3.Connection) -> None:
        """Rebuild the matrix in place from the current candidates table."""
        candidates = q_candidates.list_all(conn)
        rebuilt = self._fit(candidates)
        self.vectorizer = rebuilt.vectorizer
        self.matrix = rebuilt.matrix
        self.archive_ids = rebuilt.archive_ids
        self._id_to_row = rebuilt._id_to_row

    # --- persistence --------------------------------------------------

    def save(self, path: Path) -> None:
        """Pickle to ``path`` atomically (write-temp then rename)."""
        saved = _SavedIndex(
            magic=_PICKLE_MAGIC,
            sklearn_version=sklearn.__version__,
            vectorizer=self.vectorizer,
            matrix=self.matrix,
            archive_ids=self.archive_ids,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as f:
            pickle.dump(saved, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> TFIDFIndex:
        """Deserialize a previously saved index.

        Raises ``TFIDFPickleError`` on magic / sklearn-version mismatch so
        the caller can fall back to ``build()`` instead of guessing at a
        corrupt pickle.
        """
        with path.open("rb") as f:
            saved = pickle.load(f)
        if not isinstance(saved, _SavedIndex) or saved.magic != _PICKLE_MAGIC:
            raise TFIDFPickleError(f"{path} is not an archive-agent TF-IDF index")
        if saved.sklearn_version != sklearn.__version__:
            raise TFIDFPickleError(
                f"{path} was saved with scikit-learn {saved.sklearn_version}; "
                f"current is {sklearn.__version__}. Rebuild the index."
            )
        return cls(saved.vectorizer, saved.matrix, saved.archive_ids)


def load_or_build(conn: sqlite3.Connection, path: Path) -> TFIDFIndex:
    """Load a saved index if available, else fit a fresh one.

    Used on daemon startup. Silently rebuilds on any pickle error —
    the caller just wants an index; the cache is best-effort.
    """
    if path.exists():
        try:
            return TFIDFIndex.load(path)
        except (TFIDFPickleError, pickle.UnpicklingError, EOFError, AttributeError):
            pass
    return TFIDFIndex.build(conn)


__all__ = ["TFIDFIndex", "TFIDFPickleError", "load_or_build"]
