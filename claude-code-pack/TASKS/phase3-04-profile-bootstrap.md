# phase3-04: Profile bootstrap

## Goal

Build the very first `TasteProfile` from zero, using the Jellyfin
playback history already sitting in `episode_watches` and whatever
movie-level `taste_events` the binge aggregator (phase3-01) has
produced. Without this, phase3-03's reranker has no priors to work
from — every recommendation would be a cold-start guess.

Bootstrap runs once per install (and can be re-run explicitly if
the user wants a profile reset).

## Prerequisites

- phase1-03 (taste_profile table)
- phase1-04 (Jellyfin history sync populates episode_watches)
- phase3-01 (show-state aggregator)

## Inputs

- `CONTRACTS.md` §1 TasteProfile, §2 `LLMProvider.update_profile`
- ADR-003 (unified profile), ADR-004 (binge is signal)
- `config.llm.ollama.model` (qwen2.5:7b by default)
- Existing `taste_events` rows (movies + binge-derived show events)
- Jellyfin history metadata for *titles and genres* (not just IDs) —
  the bootstrap needs to pass titles into the prompt so the LLM can
  reason about taste

## Deliverables

1. `src/archive_agent/taste/bootstrap.py`:

   ```python
   class BootstrapInput(BaseModel):
       finished_movies: list[Candidate]
       abandoned_movies: list[Candidate]
       rewatched_movies: list[Candidate]
       binge_positive_shows: list[Candidate]      # one row per show
       binge_negative_shows: list[Candidate]
       explicit_ratings: dict[str, TasteEvent]    # show_id → latest rating
       total_events: int

   async def gather_bootstrap_input(
       conn: sqlite3.Connection,
   ) -> BootstrapInput:
       """Read taste_events + candidates + ratings. Join to surface
       titles and genres (profile can't be built from IDs alone).
       Deduplicate: multiple finishes of the same movie collapse to
       one rewatched_movies entry."""

   async def bootstrap_profile(
       conn: sqlite3.Connection,
       provider: LLMProvider,
       *,
       dry_run: bool = False,
   ) -> TasteProfile:
       """Build BootstrapInput, call provider.update_profile(
       current=empty_profile, events=synthesized_events), persist
       result as profile version 1. If dry_run=True, return the
       profile without writing to DB."""
   ```

2. Empty-profile sentinel:

   ```python
   def empty_profile() -> TasteProfile:
       return TasteProfile(
           version=0,
           updated_at=datetime.now(UTC),
           summary="No playback history yet.",
       )
   ```

3. Prompt additions for `update_profile` (shared with phase3-05 —
   this card defines the prompt; phase3-05 uses the same one):
   `src/archive_agent/ranking/prompts/profile.j2`. Must cover:
   - Current profile (if non-empty) prose + lists
   - New events: grouped by kind with titles, genres, years
   - Rating events (ADR-013) surfaced as strong priors in prose
   - Instructions: output a full `TasteProfile` JSON; preserve IDs;
     summary prose ≤ 500 words; preserve tone that reads like a
     human description, not bullet points

4. `state/queries/taste.py`:

   ```python
   async def get_latest_profile(conn) -> TasteProfile | None:
       """Newest by version DESC. None if none."""

   async def insert_profile(conn, profile: TasteProfile) -> None:
       """Append-only. Never update an existing version — always
       insert with version = (max existing version + 1)."""
   ```

   (`insert_profile` already exists from phase1-03 in skeleton form;
   confirm signature matches and extend only if needed.)

5. CLI:
   - `archive-agent taste bootstrap [--dry-run]` — runs the
     bootstrap, prints the resulting profile summary, asks for
     y/N confirmation before inserting (skip prompt with `--yes`)
   - `archive-agent taste show-profile` — pretty-prints the
     latest profile (version, summary, top 10 liked genres,
     liked/disliked ID counts)

6. Tests in `tests/unit/taste/`:
   - `test_gather_bootstrap_input.py` — fixture with 20 finished
     movies across 3 genres, 2 abandoned, 5 binge-positive shows;
     assert correct bucketing, title/genre join, and rating lookup
   - `test_bootstrap_profile.py` — uses a fake LLMProvider that
     returns a fixed profile; asserts the inserted row has
     version=1 and the summary matches

7. Integration test (skipped unless `RUN_INTEGRATION_TESTS=1`):
   - Real Ollama, real fixture `taste_events` — asserts profile has
     non-empty summary, version=1, liked_genres non-empty

## Done when

- [ ] `archive-agent taste bootstrap --dry-run` prints a plausible
  profile without writing to DB
- [ ] `archive-agent taste bootstrap --yes` writes version=1
- [ ] Re-running bootstrap without `--yes` refuses because a
  profile already exists (unless `--force` is passed)
- [ ] Explicit ratings (ADR-013) appear as priors in the generated
  summary prose
- [ ] `mypy --strict` passes
- [ ] Unit tests pass; integration optional

## Verification commands

```bash
archive-agent jellyfin sync
archive-agent taste aggregate
archive-agent taste bootstrap --dry-run
archive-agent taste bootstrap --yes
archive-agent taste show-profile
```

## Out of scope

- Rebuilds / incremental updates — that's phase3-05
- UI for reviewing the profile before saving — CLI y/N confirmation
  is enough for v1
- Multi-user profiles — ADR-007 (single shared profile for now)

## Notes

- Events are synthesized, not re-used: `gather_bootstrap_input`
  collects the *results* of prior taste events, but the call to
  `update_profile` passes a fresh `list[TasteEvent]` built from
  the bucketed buckets. This keeps the update_profile prompt
  uniform across bootstrap and incremental paths (phase3-05).
- If a user has truly zero playback history, bootstrap should still
  produce a valid profile — just with a summary like "No playback
  history yet; using household default of broad tastes." Don't block
  the cold-start path.
- Ratings without corresponding implicit signal still count as strong
  priors. A user who launches on day one, rates 5 shows 👍👍 and
  nothing else should get a profile that weights those shows heavily.
- The `dry_run` flag is important: the bootstrap LLM call is cheap
  but not free, and the user may want to eyeball the output before
  committing it.
