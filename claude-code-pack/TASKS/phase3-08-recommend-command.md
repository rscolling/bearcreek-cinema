# phase3-08: `archive-agent recommend` end-to-end

## Goal

Wire the phase 3 parts together into one cohesive command so a human
(and later the HTTP API — phase 4) can ask: "give me 5 things to
watch." The command reads the latest `TasteProfile`, runs the
two-stage pipeline (TF-IDF prefilter → LLM rerank), promotes the
returned candidates from `new`/`ranked` → `ranked`/`approved` in
state, and prints a pretty shortlist with reasoning.

This is the card that makes Phase 3 real.

## Prerequisites

- phase3-01 (aggregator), phase3-02 (prefilter), phase3-03 (Ollama
  rank), phase3-04 (bootstrap), phase3-05 (update), phase3-06
  (TFIDF fallback)
- phase3-07 is optional but wires cleanly if present

## Inputs

- `CONTRACTS.md` §4 CLI shape
- `config.recommend.{default_n, prefilter_k, exclude_window_days}`
  — the last one excludes anything already recommended recently
- Latest `TasteProfile`, current `candidates` pool, latest ratings
  (ADR-013)

## Deliverables

1. `src/archive_agent/commands/recommend.py`:

   ```python
   class RecommendResult(BaseModel):
       n_requested: int
       n_returned: int
       provider: Literal["ollama", "claude", "tfidf"]
       items: list[RankedCandidate]
       profile_version: int
       elapsed_ms: int
       fallbacks: list[str]        # e.g., ["ollama→tfidf: timeout"]

   async def recommend(
       conn: sqlite3.Connection,
       config: Config,
       *,
       n: int = 5,
       content_types: list[ContentType] | None = None,
       force_provider: Literal["ollama", "claude", "tfidf"] | None = None,
   ) -> RecommendResult:
       """End-to-end pipeline:
       1. Load latest profile (error clearly if none — suggest
          `archive-agent taste bootstrap`)
       2. Load latest-rating map for all shows (ADR-013)
       3. Compute exclude_archive_ids: any candidate that's been
          ranked/approved/committed in the last exclude_window_days
       4. Run prefilter(k=config.recommend.prefilter_k)
       5. Select provider via factory.provider_for_workflow("rank")
          (respects force_provider override)
       6. provider.rank(profile, shortlist, n=n, ratings=rating_map)
       7. Promote status: candidates returned → status='ranked';
          persist RankedCandidate rows into a new table
          `ranked_candidates` (see deliverable 2) for audit
       8. Return RecommendResult
       """
   ```

2. New state table for rank audit (migration `005_ranked_candidates.py`):

   ```sql
   CREATE TABLE ranked_candidates (
       id            INTEGER PRIMARY KEY,
       batch_id      TEXT NOT NULL,       -- uuid4 per recommend() run
       archive_id    TEXT NOT NULL,
       rank          INTEGER NOT NULL,
       score         REAL NOT NULL,
       reasoning     TEXT NOT NULL,
       provider      TEXT NOT NULL,
       profile_version INTEGER NOT NULL,
       created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
       FOREIGN KEY (archive_id) REFERENCES candidates(archive_id)
   );
   CREATE INDEX idx_ranked_candidates_batch ON ranked_candidates(batch_id);
   CREATE INDEX idx_ranked_candidates_archive ON ranked_candidates(archive_id);
   ```

   Queries in `state/queries/ranked.py`:
   - `insert_batch(conn, batch_id, items, provider, profile_version)`
   - `latest_batch(conn) -> list[RankedCandidate]`
   - `recent_archive_ids(conn, since: datetime) -> set[str]`

3. CLI: `archive-agent recommend` (Typer subcommand)

   ```
   Usage: archive-agent recommend [OPTIONS]

     Produce a ranked shortlist of candidates.

   Options:
     --n INTEGER                 Number of picks [default: 5]
     --type [movie|show|any]     Filter [default: any]
     --provider [ollama|claude|tfidf]   Force provider
     --json                      Machine-readable output
     --dry-run                   Don't promote status in DB
   ```

   Default output is a Rich-formatted table:
   ```
    # │ Title                       │ Year │ Type  │ Score │ Reasoning
   ───┼─────────────────────────────┼──────┼───────┼───────┼──────────────────
    1 │ His Girl Friday             │ 1940 │ movie │ 0.91  │ Screwball pacing...
    2 │ The Dick Van Dyke Show S1   │ 1961 │ show  │ 0.88  │ TV companion...
   ```

4. Loop integration: the daemon loop runs `recommend()` on a
   configurable interval (default 6 hours) and stores results. The
   HTTP API (phase 4) will read `latest_batch` — not call
   `recommend()` on the request hot path.

5. Tests in `tests/unit/commands/test_recommend.py`:
   - Happy path: profile exists, candidates exist, Ollama mocked to
     return 5 picks → `items` has 5, `fallbacks=[]`, status
     promotions land in DB
   - No profile: raises `NoProfileError` with actionable message
   - Empty candidate pool after prefilter: returns empty items,
     provider unchanged, no error
   - `force_provider="tfidf"`: skips Ollama, returns 5 picks with
     templated reasoning
   - Ollama fails → TFIDF fallback: `fallbacks` contains the hop,
     5 picks still returned
   - `exclude_window_days`: candidate ranked 3 days ago is excluded
   - `dry_run=True`: no DB writes, but `items` populated

6. Integration test (opt-in): run the full command against a real
   fixture DB with 500 candidates + fake profile; assert
   end-to-end latency < 10s with Ollama.

## Done when

- [ ] `archive-agent recommend --n 5` returns a 5-item mixed
  movie+TV shortlist with LLM reasoning on a freshly-bootstrapped
  profile
- [ ] `archive-agent recommend --provider tfidf` works with Ollama
  stopped
- [ ] `ranked_candidates` table has rows for every run
- [ ] `--dry-run` never writes to DB
- [ ] Re-running within `exclude_window_days` doesn't repeat picks
- [ ] `mypy --strict` passes
- [ ] Unit tests pass; integration optional

## Verification commands

```bash
# Assume prior phase 3 cards have landed
archive-agent taste bootstrap --yes
archive-agent recommend --n 5
archive-agent recommend --n 5 --type movie
archive-agent recommend --provider tfidf --n 3
sqlite3 $STATE_DB "SELECT batch_id, provider, COUNT(*) FROM ranked_candidates GROUP BY batch_id ORDER BY 1 DESC LIMIT 3;"
```

## Out of scope

- `/select` endpoint / download trigger — that's phase4-04
- Downloading what got recommended — happens only after the
  Roku-side `/select` in phase 4
- Cross-batch deduplication beyond `exclude_window_days` — the
  window is the mechanism; no separate logic
- Automatic retry of failed batches — if one recommend() call
  fails, the next scheduled run handles it

## Notes

- Phase 3 done-when lives here: "`archive-agent recommend` returns
  a mixed movie+TV shortlist with LLM-generated reasoning, and
  `archive-agent search fts "..."` returns typo-tolerant matches"
  (phase3-09 handles the second half).
- `exclude_window_days` (default 14) keeps the Roku poster wall
  from recycling the same 5 picks day after day. Shorter windows
  mean faster re-offer; longer means more variety but risk running
  out of fresh ideas for niche households.
- `RecommendResult.fallbacks` is what the HTTP API surfaces to the
  Roku app — lets the UI say "Running on local fallback — setup
  may need attention" without phoning home diagnostics.
- The loop runs `recommend()` *after* the aggregator and the
  profile-update step, so fresh signal influences the next batch.
- Store the batch_id in the candidate's current status transition
  (via `state.queries.candidates.update_status(... batch_id=...)`)
  so we can trace which batch promoted a given candidate. Avoids
  ambiguity when two overlapping batches touch the same candidate.
