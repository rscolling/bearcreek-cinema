# SESSION

**Last updated:** 2026-04-19 by phase2-08 tv-sampler (sampler-first capstone — decide_for_show + step_show + season advancement). **Phase 2 complete.**

Cross-session continuity for Claude Code working on Bear Creek Cinema.
Read at the start of every session. Updated at the end of every session.
If this file is stale, fix it or delete the stale section — wrong
information here is worse than no information.

This file is *ephemeral operational state only*. Architectural decisions
go to `claude-code-pack/DECISIONS.md`. Bugs go to GitHub issues. Task
progress goes to the checklist in `claude-code-pack/TASKS/README.md`.

---

## Current status

**Phase:** **Phase 1 complete.** Scaffold, Ollama stack, config loader,
state DB, Jellyfin client, LLMProvider skeleton, and logging +
observability are all landed.

**Active task:** None. **Phase 2 complete** — all 9 cards done.
`archive-agent download <id>` → place → scan → resolve → Jellyfin
indexed; `librarian evict` keeps disk under budget; `tv step`
drives the sampler state machine end-to-end.

Phase 3 opens next (ranking + taste profile):

- `phase3-01-show-state-aggregator` — episode_watches → binge events
- `phase3-02-tfidf-prefilter` — cosine similarity over candidates
- `phase3-03-ollama-rank` — LLM reranker (real impl of
  OllamaProvider.rank)
- `phase3-04-profile-bootstrap` — initial taste profile from history
- `phase3-05-profile-update` — incremental
- `phase3-06-tfidf-provider` — full TFIDFProvider impl
- `phase3-07-claude-rank` — full ClaudeProvider impl
- `phase3-08-recommend-command` — `archive-agent recommend` wires it
- `phase3-09-fts5-indexing` — SQLite FTS5 virtual table for search

None of phase 3 have written cards yet beyond roadmap entries in
`TASKS/README.md`. Drafting them is the natural next step.

Phase 2 done when: `archive-agent download <movie-id>` produces a
playable file in Jellyfin; librarian enforces budget and evicts
ephemeral content; TV sampler flow promotes one show end-to-end.

**Codebase state:** Python package live at `src/archive_agent/` with
stub CLI (10 command groups, all exit 1), `tests/` scaffold with two
smoke tests, `docker/Dockerfile` + `docker-compose.yml` + `.dockerignore`
for the prod target, plus `docker/ollama.compose.yml` as a reference
mirror of the Ollama stack deployed on don-quixote. `pyproject.toml`
declares deps + mypy-strict + ruff. Pre-commit covers
ruff/ruff-format/mypy/pytest-unit. `.venv/` on blueridge has the
package installed editable with dev extras.

**Deployed infra on don-quixote:**

- `/home/blueridge/ollama/` — `ollama` container running
  `ollama/ollama:latest`, healthy, published on `:11434`, with
  `qwen2.5:7b` (4.7 GB) and `llama3.2:3b` (2.0 GB) pre-pulled into
  the `ollama_ollama_models` named volume. `OLLAMA_KEEP_ALIVE=1h`.
- `ollama_default` Docker network exists; archive-agent compose will
  join it as `external: true` when deployed.
- First `qwen2.5:7b` prompt took ~3s eval / ~11s total incl. cold load.
- Agent compose not yet deployed (phase1-02+ needed first).

**Credentials (`.env` on blueridge, gitignored):**

- `TMDB_API_KEY` — validated (HTTP 200 on `/3/configuration`).
- `JELLYFIN_API_KEY` + `JELLYFIN_USER_ID` — validated (HTTP 200 on
  `/Users/{uid}`; user `colling`, admin, GUID `7dc32a...6214`). Note:
  Jellyfin is on 10.11.8 (newer than the 10.9.8 mentioned in docs).
  Library has 261 movies/episodes but essentially zero playback
  history — `phase3-04` bootstrap will produce a generic profile
  until real plays accumulate.
- `ANTHROPIC_API_KEY` — not set; only needed if ClaudeProvider is
  enabled for a workflow.

---

## Blockers / waiting on

- **User decision:** final repo name for the sibling RAG project
  (`claude-docs-rag` is the working name, alternatives discussed)
- **Watch-history cold start:** household hasn't accumulated playback
  on this Jellyfin instance yet. Not a blocker for phase1/2/3 code,
  but `phase3-04` profile bootstrap will be thin until real plays
  arrive; may need a manual-seed flow.

---

## Recent sessions

*Most recent first. Prune entries older than the last 5 retained.*

### 2026-04-19 — phase2-08: TV sampler capstone — Phase 2 complete

- `librarian/tv_sampler.py` — `decide_for_show` (pure state machine),
  `step_show` (executes with injectable Downloader protocol), and
  `step_all_shows` (serial iteration so we don't spray Archive.org
  with parallel requests across shows).
- The decision table: no state → **start_sampling** (first N S1
  episodes eligible); sampler partial → **wait**; sampler complete
  and `should_promote` true → **promote** (queue rest of S1 into tv,
  move sampler folder); sampler complete, within window, not
  enough watches → **wait**; sampler complete, past window →
  **evict** (phase2-07 does the actual sweep); committed through
  season `N`, any episode watched, season `N+1` available →
  **promote** (advance season); committed without watches →
  **wait**.
- `should_promote` enforces both the ≥2 finished count **and** that
  last_playback-started window is ≤14d — so a household that watched
  2 episodes 25 days after the sampler started doesn't promote
  (window is current-interest, not cumulative-interest).
- Episode-1-missing slide-forward handled per ARCHITECTURE.md:
  sampler takes the first N S1 episodes that exist as candidates.
  Queue remainder step filters out already-handled (season, episode)
  pairs so a re-run doesn't re-enqueue the sampler set.
- Circular import fix: `archive.downloader` needs `librarian.zones`
  and tv_sampler wants DownloadRequest/DownloadResult — the
  downloader types sit behind `TYPE_CHECKING` and `DownloadRequest`
  imports live inside the function body. `Downloader` is a Protocol
  (not a Callable alias) so forward refs work at runtime.
- CLI: `tv step <id>`, `tv sample <id>` (alias), `tv status` (prints
  decide_for_show's current verdict for every show with episode
  candidates).
- Added `queries.candidates.list_by_show` for the sampler to
  enumerate a show's episodes.
- **21 new tests** (16 decision-table covering every branch of the
  state machine incl. the window-is-delta regression; 5 step_show
  execution covering start-sampling + promote + wait + evict +
  download-failure-tolerated). **301 unit total**, mypy --strict
  clean on 52 files.

### 2026-04-19 — phase2 cards 07 + 09 (eviction, jellyfin placement)

- **phase2-07 eviction** (commit `26cac40`) — `plan_eviction`
  walks /media/recommendations (14d TTL) + /media/tv-sampler (30d
  TTL) when agent usage is over `max_disk_gb`, picks oldest-stale
  folders first, stops at overage. `execute_eviction` rmtrees the
  plan items, writes `librarian_actions action='evict'` rows, sets
  `candidates.status=EXPIRED` when the folder maps back to a row.
  Touch-time precedence: `show_state.last_playback_at` >
  `candidate.discovered_at` > filesystem mtime. Never atime.
  Hard-guardrails encoded in code: /media/movies never in the plan,
  /media/tv requires explicit propose+grace (stub
  `propose_committed_tv_eviction` writes an `action='skip'` row
  with `reason=committed_eviction_proposed:grace_days=N`). Blocked
  plans emit a loud `eviction_blocked` WARN even on --dry-run.
  20 new tests (plan + execute + blocked paths; capsys over stderr
  because `configure_logging(force=True)` resets pytest's caplog)
- **phase2-09 jellyfin-placement** (commit `<next>`) —
  `resolve_libraries` maps the four expected /media paths to
  Jellyfin library ids via `/Library/VirtualFolders`. Loud
  `MissingLibraryError` when any zone lacks a library, pointing at
  the new ENVIRONMENT.md setup checklist (user creates
  "Recommendations" and "TV Sampler" custom libraries at first
  deploy). `_find_item_for_candidate` scopes searches to
  `ParentId=<library_id>` so the same film in movies vs
  recommendations doesn't cross-match. `scan_and_resolve`
  triggers a scan, polls every 2s up to 90s, writes
  `candidates.jellyfin_item_id`, returns None + WARN on timeout.
  `scan_zones` batches library refreshes with dedup. CLI:
  `jellyfin scan [--zone=all|...]` + `jellyfin resolve <id>`.
  19 new tests incl. path normalization (Windows slashes / case /
  trailing slash), ItemId-vs-Id back-compat, title+year
  disambiguation (The Lost World 1925 vs 1960), episode
  season/episode matching, and scan_and_resolve timeout path.
  **278 unit total**, mypy --strict clean on 51 source files

### 2026-04-19 — phase2 cards 03 + 06 (tv-grouping, placement)

- **phase2-03 tv-grouping** (commit `cafdb7b`) — four-tier confidence
  ladder: high (SxEy marker + TMDb match), medium (single TMDb hit
  without marker), low (multiple TMDb hits — queue for review),
  none (no TMDb match / title empty after marker strip). Only high
  and medium write back to the candidate row; low/none land in the
  new `tv_grouping_review` table (migration 004). Regex covers 10
  title patterns (S01E03, s1e3, 1x03, Season 1 Episode 3, - Ep 03 -,
  Episode 3, etc.) — tested via parametrize. Added
  `TmdbClient.search_shows` (plural) so the grouper can count results
  for medium-vs-low decisions. Live on real IA data: "Pete Seeger's
  Rainbow Quest, Episode 14: Political songs" classified to show
  20720 ("Rainbow Quest") S01E14 confidence=high — full round trip
  through parse → TMDb → DB writeback. Most 1950s-60s IA TV items
  don't carry SxEy markers and correctly land in the review queue
- **phase2-06 librarian-placement** (commit `<next>`) — the ONLY
  module in the agent that `shutil.move`s under `/media/*`.
  `place()` rejects USER_OWNED zones directly and forces budget
  headroom check before moving (raises `BudgetExceededError` with
  the exact overage bytes). `promote_movie` (recommendations →
  movies) and `promote_show` (tv-sampler → tv) migrate whole folders
  so subtitles/metadata sidecars stay with the video. Disambiguation
  appends `(N)` on filename or folder collision. Jellyfin naming
  helpers (`jellyfin_movie_folder`, `jellyfin_episode_filename`, etc.)
  plus a hand-rolled `sanitize_filename` (no new dep needed). CLI:
  `librarian place <id> [--zone] [--dry-run]` and `librarian promote
  <id> [--dry-run]` which dispatches on content_type. **41 new
  tests** (16 naming incl. Windows-forbidden chars and trailing dots;
  10 placement incl. budget rejection and dry-run side-effect-free;
  9 promote incl. missing-source + disambiguation + show_id-as-
  folder fallback). **220 unit total**, mypy --strict clean on 49
  files

### 2026-04-19 — phase2 batch (cards 05 → 02 → 04, landed serially)

User asked for parallel execution via worktree-isolated subagents;
the harness refused (no `WorktreeCreate` hook configured). Fell back
to sequential in-session execution.

- **phase2-05 librarian-core** (commit `ac2b55b`) — 3 modules + CLI.
  Zone StrEnum whose values match the on-disk directory names (the
  `downloads.zone` CHECK constraint depends on this); AGENT_MANAGED
  / USER_OWNED frozensets so "never auto-evict /media/movies" is a
  set-membership test (`every_zone_is_categorized` test guards the
  invariant). `scan_zone` tolerates missing paths and per-file
  permission errors. `log_action` writes to `librarian_actions` with
  UTC timestamp. CLI: `archive-agent librarian status` prints the
  BudgetReport JSON + a one-line human summary tagging
  `/media/movies` as user-owned. 18 new tests, live-verified on a
  scratch zone tree
- **phase2-02 metadata enrichment** (commit `20a28a6`) — TmdbClient
  (httpx async context manager over the real TMDb v3 API), cache
  module backed by migration 003's `metadata_cache` table, and
  `enrich_candidate` / `enrich_new_candidates`. Non-overwrite
  contract: Archive.org's curated fields win if present. Search
  includes `primary_release_year` / `first_air_date_year` to
  disambiguate same-title films across decades. 429 + persistent-5xx
  handling with exponential backoff. 25 new tests + 2 integration;
  141 unit total. Live on 5 real candidates: 2 filled (Raiders of
  Old California, Little Men), 3 TMDb misses; re-run finished in
  <1 s (cache hits). **Found and fixed a real secret leak**: httpx
  and httpcore log full request URLs at INFO, which bypass the
  structlog redactor — TMDb's `?api_key=<key>` showed up in stdout.
  `configure_logging` now clamps those loggers to WARNING unless
  the overall level is DEBUG
- **phase2-04 downloader** (commit `<next>`) — `DownloadRequest` /
  `DownloadResult` / `download_one`, backend dispatch between
  `ia-get` (subprocess) and Python `internetarchive` library (always
  available). `pick_format` walks a preference list (h.264 → MPEG4
  → Matroska → Ogg Video) and prefers `source=original` over IA
  re-encodes. Module-level asyncio.Semaphore for concurrency. Row
  lifecycle: `queued → downloading → done | failed | aborted`;
  `done` short-circuits on retry, `failed`/`aborted` resets to
  queued. 14 new tests + 1 integration (159 unit, 9 integration
  total). Live download of a 3 MB Turner short confirmed; second
  run correctly reported `status=skipped`. A couple of stubs
  debugged end-to-end: picked the wrong `File.download` kwarg
  (`file_path=` is undefined across versions; `destdir=` is the
  portable one) — test failed, inspected, fixed

### 2026-04-19 — phase2-01: Archive.org discovery live

- `archive/search.py` — `search_collection(collection, ...)` yields
  normalized `ArchiveSearchResult`s from `internetarchive.search_items`.
  Runs the sync library call in a thread so we don't stall the loop.
  Defensive parsing: runtime strings (`"1:07:39"` → 67, `"Approx 30
  Minutes"` → 30, `"25:17"` → 25, unparseable → None); `subject`
  coerces scalar-or-list; `year` coerces int-or-string. The raw IA
  search returns a lot more fields than we model; `extra='ignore'`
  keeps us resilient to schema drift
- `archive/discovery.py` — `discover(conn, config, collection=, limit=)`
  wires search → `search_result_to_candidate` → `upsert_candidate`.
  Content-type heuristic: `moviesandfilms` → MOVIE,
  `television` → EPISODE (phase2-03 will reclassify some as SHOW).
  Genre normalization: lowercase + dedup + sort.
  `_merge_status` preserves a candidate's existing status on
  re-discovery so we never roll APPROVED / DOWNLOADING / etc. back to NEW
- `DiscoverResult` reports inserted / updated / skipped_quality /
  skipped_year / by_collection counters
- CLI: `archive-agent discover [--collection ...] [--limit N]` replaces
  the stub
- Tests: 21 new (11 search-parsing including every runtime format
  variant and scalar-subject coercion; 10 discovery including
  idempotency, single-vs-both collections, status preservation,
  quality/year rejection counters). Integration: 2 live tests
  (one per collection, limit=3). Total: 98 unit + 6 integration
- Live run against Archive.org: `limit=10` on moviesandfilms →
  inserted 10; second run → updated 10, inserted 0. Real titles land
  (*Meet John Doe* 1941, *Ministry Of Fear* 1944, *Panther's Claw*
  1942, ...)
- Ticked `phase2-01` in `TASKS/README.md`

---

## Protocol for Claude Code

**At session start:**

1. Read this file first
2. Cross-check "Current status" and "Blockers" against reality if
   possible (does the described codebase state match `git status`?)
3. Note any drift; if found, either fix the drift or update this file
   to match reality before starting new work

**At session end:**

1. Update "Last updated" timestamp
2. Update "Current status" to reflect where things actually stand
3. Update "Blockers / waiting on" — add new blockers, remove resolved
   ones
4. Prepend a new "Recent sessions" entry with date, short description,
   and outcome
5. Prune "Recent sessions" to the most recent 5 entries
6. Never backdate entries or invent outcomes

**If a session ended abnormally** (crashed, interrupted, ran out of
context mid-task):

- Leave "Current status" honest: "mid-edit on `jellyfin/client.py`,
  auth function partial" is more useful than pretending things are
  clean
- Add a blocker: "abnormal session end — verify no broken state in
  working directory"

**If you're unsure whether something belongs here:**

- Permanent fact? → goes in a permanent doc (ARCHITECTURE.md,
  DECISIONS.md, design-principles.md, etc.)
- Task-level progress? → goes in `claude-code-pack/TASKS/README.md`
  checklist
- Bug or issue? → GitHub issue
- Ephemeral "here's where we are right now"? → here
