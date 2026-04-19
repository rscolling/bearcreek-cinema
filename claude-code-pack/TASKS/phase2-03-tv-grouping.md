# phase2-03: TV episode → show grouping

## Goal

Archive.org's television collection is messy: sometimes a whole series
is one item with 24 video files; sometimes each episode is its own
item; sometimes both exist for the same show. Group loose episode
candidates into shows so the ranker can reason about "I like *The Dick
Van Dyke Show*" instead of "I like this one random episode."

See ARCHITECTURE.md §"Why movies and TV in parallel" and
`docs/search-and-retrieval.md` for why this is hard and why
low-confidence matches get flagged rather than force-grouped.

## Prerequisites

- phase2-01 (candidates table populated with `EPISODE`-type rows)
- phase2-02 (TMDb client — we use `/search/tv` + `/tv/{id}/season/{n}`)
- phase1-03 (state DB; may add a column or table here)

## Inputs

- `candidates` rows with `content_type=EPISODE` and `source_collection="television"`
- TMDb TV endpoints

## Deliverables

1. `src/archive_agent/archive/tv_grouping.py`:

   ```python
   class GroupingMatch(BaseModel):
       archive_id: str
       show_id: str | None            # TMDb show id on match, None otherwise
       season: int | None
       episode: int | None
       confidence: Literal["high", "medium", "low", "none"]
       reason: str                    # short human-readable explanation

   async def classify_episode(
       candidate: Candidate,
       tmdb: TmdbClient,
   ) -> GroupingMatch:
       """Try in order:
       1. If the Archive.org item already has show_id set (rare), trust it.
       2. Regex for S01E03 / s1e3 / Season 1 Episode 3 / 1x03 patterns
          in the title. If found + TMDb hit on the title prefix →
          confidence=high.
       3. Plain TMDb /search/tv on the title (strip the S/E suffix if any).
          If exactly one result → confidence=medium.
       4. TMDb search returns multiple → confidence=low (pick first, flag).
       5. TMDb returns nothing → confidence=none (standalone episode)."""

   async def group_unassigned_episodes(
       conn: sqlite3.Connection,
       tmdb: TmdbClient,
       *,
       limit: int | None = None,
   ) -> GroupingResult:
       """Run classify_episode over every EPISODE candidate whose show_id
       is NULL. Writes show_id, season, episode back to candidates;
       records low/none matches in the review queue (see deliverable 3)."""

   class GroupingResult(BaseModel):
       classified: int
       high: int
       medium: int
       low: int
       none: int
   ```

2. Title-parse helpers with a shared test matrix:

   ```python
   class SxEy(NamedTuple):
       season: int
       episode: int
       title_prefix: str              # title with the S/E token removed

   def parse_episode_marker(title: str) -> SxEy | None: ...
   ```

   Must recognize at minimum: `S01E03`, `s1e03`, `Season 1 Episode 3`,
   `1x03`, `- Ep 03 -`. Test each in
   `tests/unit/archive/test_tv_grouping.py::test_episode_marker_patterns`.

3. Migration 004: add a review-queue table for low/none matches.

   ```sql
   CREATE TABLE tv_grouping_review (
     archive_id TEXT PRIMARY KEY,
     confidence TEXT NOT NULL CHECK (confidence IN ('low', 'none')),
     reason TEXT NOT NULL,
     suggested_show_id TEXT,
     created_at TEXT NOT NULL,
     reviewed_at TEXT,                -- NULL = unresolved
     reviewed_by TEXT                 -- free-form ('manual', 'phase6', etc.)
   );
   ```

4. CLI:
   - `archive-agent tv-grouping run [--limit N]` — runs
     `group_unassigned_episodes`, prints a `GroupingResult`
   - `archive-agent tv-grouping review` — prints the review queue

5. Tests:
   - `tests/unit/archive/test_tv_grouping.py` — marker parsing (at
     least 10 title variants), classification confidence for each
     branch, assignment to show_id on high-confidence, review-queue
     insertion on low/none
   - Fixture: `claude-code-pack/fixtures/sample_archive_search.json`
     already contains a deliberate ambiguous TV item (per INVENTORY.md);
     use that as the low-confidence case

## Done when

- [ ] `archive-agent tv-grouping run` populates `show_id`, `season`,
  `episode` on at least some existing EPISODE candidates
- [ ] Marker regex recognizes all ten documented patterns
- [ ] Low/none-confidence rows land in `tv_grouping_review`
- [ ] Re-running doesn't re-classify already-grouped items
- [ ] `mypy --strict` passes on `archive/tv_grouping.py`
- [ ] Tests pass

## Verification commands

```bash
archive-agent discover --collection television --limit 50
archive-agent metadata enrich --limit 50
archive-agent tv-grouping run --limit 50
sqlite3 $STATE_DB \
  "SELECT show_id IS NULL AS ungrouped, COUNT(*) FROM candidates \
   WHERE content_type='episode' GROUP BY 1"

archive-agent tv-grouping review | head -20
```

## Out of scope

- Actually resolving low-confidence items — they sit in the review
  queue until phase6 (or manual curation)
- Grouping movies into collections (boxsets) — different problem;
  deferred
- TVDB / IMDb lookups as fallback — TMDb's TV coverage is good for
  the public-domain-era shows we care about

## Notes

- Archive.org's multi-file-per-item television uploads often list
  episodes as individual `.mp4` files inside one `details/<show>` page.
  phase2-04's downloader understands this shape. For grouping purposes,
  treat each file as its own conceptual episode — but the
  `archive_id` may point at the containing item, with a secondary file
  identifier tracked elsewhere. If this gets hairy, add a `file_ref`
  column in a later migration; don't force it now.
- A `show_id` in our DB is the TMDb numeric id stored as string
  (e.g., `"1433"` for *The Dick Van Dyke Show*). The Jellyfin bootstrap
  path uses the `jellyfin:<guid>` prefix (phase1-04); TMDb-derived IDs
  here are namespaced by being plain numeric strings. Downstream code
  must not assume either format — treat `show_id` as opaque.
- Keep the parse regex conservative: false positives (grouping an
  episode into the wrong show) are worse than false negatives (leaving
  a standalone episode). Low confidence → review queue, don't force.
