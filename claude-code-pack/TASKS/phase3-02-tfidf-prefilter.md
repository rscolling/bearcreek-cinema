# phase3-02: TF-IDF prefilter

## Goal

Build the in-memory TF-IDF matrix over `candidates` and expose a
`prefilter(profile, candidates, k=50)` that returns the top-k most
similar items by cosine distance. This is stage 1 of the two-stage
ranking pipeline (ADR-001) — O(10^4) candidates trimmed to ~50 so the
LLM reranker (phase3-03) only sees a manageable shortlist.

## Prerequisites

- phase1-03 (state schema: `candidates` table)
- phase2-02 (TMDb enrichment — genres, description populated)

## Inputs

- `docs/search-and-retrieval.md` §"Index 2: In-memory TF-IDF"
- ADR-012 (no vector DB — scikit-learn is the right shape)
- `TasteProfile` from `CONTRACTS.md` §1

## Deliverables

1. `src/archive_agent/ranking/tfidf/features.py`:

   ```python
   def candidate_document(c: Candidate) -> str:
       """Render a candidate into a single text document for the
       vectorizer. Concatenates: title, year/decade token, genres,
       description, content_type token, runtime bucket token.
       Deterministic — same candidate always produces the same
       string."""

   def profile_document(profile: TasteProfile) -> str:
       """Render a TasteProfile into a query document: liked genres
       (weighted 2x by duplication), liked-show titles resolved via
       join, disliked content *excluded* from the query (handled in
       prefilter as a post-filter, not the query itself)."""
   ```

2. `src/archive_agent/ranking/tfidf/index.py`:

   ```python
   class TFIDFIndex:
       vectorizer: TfidfVectorizer
       matrix: sparse.csr_matrix        # shape (n_candidates, n_terms)
       archive_ids: list[str]           # parallel to matrix rows

       @classmethod
       async def build(
           cls, conn: sqlite3.Connection
       ) -> "TFIDFIndex":
           """One-shot construction: read all candidates, fit
           TfidfVectorizer(ngram_range=(1,2), min_df=2,
           stop_words='english'), store the matrix in memory."""

       async def refresh(self, conn: sqlite3.Connection) -> None:
           """Rebuild in place. Called when candidates table grows
           significantly (e.g., after phase2-01 discovery). Cheap
           at O(10^4) — a few hundred ms."""

       def save(self, path: Path) -> None:
           """Pickle to disk for warm restart (ADR-012). Called
           nightly from the loop."""

       @classmethod
       def load(cls, path: Path) -> "TFIDFIndex":
           """Deserialize. Raise a clear error if the pickle is from
           a different scikit-learn version."""
   ```

3. `src/archive_agent/ranking/tfidf/prefilter.py`:

   ```python
   def prefilter(
       index: TFIDFIndex,
       conn: sqlite3.Connection,
       profile: TasteProfile,
       *,
       k: int = 50,
       content_types: list[ContentType] | None = None,
       exclude_archive_ids: set[str] | None = None,
   ) -> list[tuple[Candidate, float]]:
       """Rank candidates by cosine similarity to profile_document(profile).
       Returns top-k (candidate, score) pairs, score in [0, 1].

       Hard filters applied AFTER scoring:
       - Drops candidates in profile.disliked_archive_ids
       - Drops candidates whose show_id is in profile.disliked_show_ids
       - Drops anything in exclude_archive_ids (already-recommended)
       - Optionally filters by content_types
       """
   ```

4. CLI:
   - `archive-agent rank prefilter [--k 50] [--type movie|show|any]`
     — reads the current profile + candidate pool, prints the top-k
     with scores. Useful for debugging TF-IDF output without
     invoking the LLM.
   - `archive-agent rank rebuild-index` — force a fresh rebuild and
     save to disk.

5. Loop integration: on daemon startup, try `TFIDFIndex.load(...)`;
   fall back to `TFIDFIndex.build(...)` on mismatch or missing file.
   Rebuild daily after discovery sweep.

6. Tests in `tests/unit/ranking/tfidf/`:
   - `test_features.py` — `candidate_document` stability and content
   - `test_index.py` — build from fixture candidates, vector shape
     sanity, save + load round-trip
   - `test_prefilter.py`:
     - Profile liking sci-fi surfaces sci-fi candidates above the rest
     - Disliked archive_ids are excluded even when score is high
     - `content_types` filter works
     - Empty profile doesn't crash — returns arbitrary k candidates
     - `k > len(candidates)` returns all available

## Done when

- [ ] `archive-agent rank prefilter --k 50` returns 50 rows in <500ms
  against a fixture of 10k candidates
- [ ] Disliked content never appears in output
- [ ] Save/load round-trip preserves ranking output bit-identical
- [ ] `mypy --strict` passes
- [ ] Tests pass

## Verification commands

```bash
# Populate candidates
archive-agent discover --limit 1000
archive-agent enrich --limit 1000

# Build and inspect
archive-agent rank rebuild-index
archive-agent rank prefilter --k 20
pytest tests/unit/ranking/tfidf/ -v
```

## Out of scope

- The LLM reranker — that's phase3-03
- Query parsing from natural language — phase3-06's responsibility
- Incremental / online updates to the matrix — full rebuild is fast
  enough at this corpus size (ADR-012)

## Notes

- `TfidfVectorizer` accepts an `analyzer` callable if you need
  something fancier. Don't reach for it unless the vanilla
  `ngram_range=(1,2)` output is obviously bad. Simpler is fine here.
- `min_df=2` discards hapax terms — important for a corpus dominated
  by unique film titles. Without it the matrix is huge and most
  dimensions are noise.
- Year → decade token: render as `"decade_1940s"` rather than `"1945"`
  so the model generalizes to nearby years. Test that "likes 1940s
  noir" recommends a 1947 film.
- Cosine similarity over L2-normalized rows is just a sparse dot
  product. Use `sklearn.metrics.pairwise.linear_kernel(query_vec,
  matrix)` — 5-10x faster than `cosine_similarity` for pre-normalized
  vectors.
- The profile→query doc on disliked content: duplicating disliked
  genres with a negative coefficient doesn't map cleanly to TF-IDF
  cosine. Handle disliked signal as a **post-filter** (drop
  already-disliked IDs; hard-exclude disliked genres only if the
  genre list is explicit). The LLM reranker (phase3-03) handles
  subtler negative signal.
