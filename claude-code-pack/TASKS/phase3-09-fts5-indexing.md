# phase3-09: FTS5 catalog search indexing

## Goal

Add SQLite FTS5 virtual table + triggers over the `candidates` table so
we have fast, typo-tolerant title/description search in place before
Phase 4's HTTP API needs it.

## Prerequisites

- phase1-03 (state schema)

## Inputs

- `docs/search-and-retrieval.md` §"Index 1: FTS5 over title and description"
- Existing `candidates` table from `CONTRACTS.md` §6

## Deliverables

1. Migration `002_fts5_candidates.py` with:
   - `candidates_fts` virtual table using `fts5(... tokenize="trigram
     remove_diacritics 1")`
   - INSERT/UPDATE/DELETE triggers on `candidates` to keep FTS in sync
   - Populate from existing rows as part of the up migration

2. `src/archive_agent/state/queries/search.py`:

   ```python
   async def fts_search(
       conn: sqlite3.Connection,
       query: str,
       limit: int = 20,
       content_type: ContentType | None = None,
   ) -> list[tuple[Candidate, float]]:
       """Return (candidate, bm25_score) tuples, ordered best first.
       Lower bm25 = better match."""

   async def fts_autocomplete(
       conn: sqlite3.Connection,
       prefix: str,
       limit: int = 10,
   ) -> list[dict[str, str]]:
       """Prefix match for type-ahead. Returns [{title, archive_id}]."""
   ```

3. CLI addition:
   - `archive-agent search fts "<query>" [--type movie|show|any]`
     — prints matches with scores

4. Tests in `tests/unit/state/test_search.py`:
   - Fixture populates ~20 candidates with varied titles
   - Exact match: "The Third Man" returns expected item with best score
   - Typo match: "thrid man" returns the same item (trigram FTS)
   - Missing-letter match: "beverly hilbillies" returns
     "The Beverly Hillbillies"
   - Content-type filter works
   - Empty result when query doesn't match

## Done when

- [ ] Migration applies cleanly and is reversible
- [ ] `archive-agent search fts "third man"` returns The Third Man
- [ ] Typo'd queries return correct matches via trigram
- [ ] Unit tests pass
- [ ] `mypy --strict` passes

## Verification

```bash
archive-agent state init
archive-agent discover --limit 100   # populate some candidates
archive-agent search fts "noir"
archive-agent search fts "thrid man"   # typo — should still match
pytest tests/unit/state/test_search.py -v
```

## Notes

- FTS5 with trigram tokenizer requires SQLite 3.34+. Verify in Python
  startup: `sqlite3.sqlite_version_info >= (3, 34, 0)`. Fail fast with
  a clear error if not.
- bm25 scores are negative; lower (more negative) = better. Normalize
  in the API layer to 0.0-1.0 positive-is-better for callers.
- Don't denormalize `content_type` into the FTS table — filter with a
  JOIN against `candidates` in the query.
- The UPDATE trigger has a subtle trap: if both `title` and `description`
  update in the same statement, make sure the trigger fires once, not
  twice. Test with a multi-column UPDATE.
