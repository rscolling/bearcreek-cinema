# SESSION

**Last updated:** 2026-04-19 by phase2 batch (cards 05, 02, 04 — librarian core, TMDb enrichment, Archive.org downloader — all landed with live verification)

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

**Active task:** None. Phase 2 in progress: 4 of 9 cards done
(01 discovery, 02 TMDb enrichment, 04 downloader, 05 librarian core).
Remaining working order:

- **Next:** `phase2-03-tv-grouping` (needs 02),
  `phase2-06-librarian-placement` (needs 04 + 05) — independent, can
  run in parallel.
- **Then:** `phase2-09-jellyfin-placement` (needs 06),
  `phase2-07-librarian-eviction` (needs 05 + 06).
- **Last:** `phase2-08-librarian-tv-sampler` (needs 04 / 05 / 06 /
  07 / 09).

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

### 2026-04-19 — Phase 2 task cards drafted (9 cards, no code)

- Wrote full task cards for every phase 2 entry already listed in
  `TASKS/README.md`:
  `phase2-01-archive-discovery` (Archive.org search, collection
  scanning, `candidates` upsert),
  `phase2-02-tmdb-enrichment` (TMDb client with SQLite cache;
  requires migration 003 for `metadata_cache`),
  `phase2-03-tv-grouping` (episode→show heuristics + SxEy parser +
  low-confidence review queue; migration 004 for `tv_grouping_review`),
  `phase2-04-ia-get-downloader` (subprocess wrapper with Python
  library fallback; format preference; concurrency governor),
  `phase2-05-librarian-core` (Zone enum, `BudgetReport`, audit
  helper),
  `phase2-06-librarian-placement` (the **only** module that calls
  `shutil.move` under `/media/*`; Jellyfin-friendly naming;
  `promote_movie` / `promote_show`),
  `phase2-07-librarian-eviction` (TTL-driven plan; hard-filter on
  `/media/movies`; committed-TV requires propose+grace path,
  deferred execution),
  `phase2-08-librarian-tv-sampler` (the decision table for
  sample / promote / wait / evict; Season-N advancement),
  `phase2-09-jellyfin-placement` (scan + item-id resolution;
  `LibraryMap` with required zone libraries; user-side setup note
  for the two custom Jellyfin libraries)
- Surfaced dependencies between cards in each card's "Prerequisites"
  section and the ordering block above
- Two migrations flagged for phase 2: 003 (metadata_cache) in
  phase2-02, 004 (tv_grouping_review) in phase2-03
- No code changes this session

### 2026-04-19 — phase1-06: structlog + redaction + llm_calls audit — Phase 1 complete

- `logging.py` — `configure_logging(level, fmt)` sets up structlog with
  json or console renderer, forces `logging.basicConfig` to override
  any prior handlers, and installs the custom `redact_processor`.
  `get_logger(name)` returns a typed BoundLogger
- Redaction rules: exact, prefix-with-underscore, or
  suffix-with-underscore against `{api_key, token, password, secret,
  authorization}`. Tightened from substring matching because the first
  version redacted `input_tokens` / `output_tokens` as `***` — they're
  counters, not secrets. New regression test guards against that
- `ranking/audit.py` — `audit_llm_call(provider, model, workflow,
  conn=)` async context manager. Times the call, classifies outcome
  (`ok` | `malformed` | `timeout` | `error` | `fallback`), writes one
  row to `llm_calls` on exit, emits a structured `event=llm_call` log
  line. Re-raises exceptions after the row is recorded — `outcome=error`
  shows up in the audit log even when the call fails loudly. `conn=None`
  is a silent no-op for scripts that want just the timing wrapper
- Refactored all three providers: OllamaProvider, ClaudeProvider,
  TFIDFProvider now route through `audit_llm_call` instead of
  hand-rolled `_log()`. Claude still has its own early-out for
  "api_key is None" so a disabled provider doesn't pollute the audit log
- CLI: `archive-agent logs tail [--lines --follow]` shells out to
  `docker compose logs archive-agent` (or journalctl) when available;
  `archive-agent llm-calls stats` summarises the audit table (totals,
  by-provider, by-outcome, p50/p95/p99 latencies per (provider,
  workflow), last N rows)
- `@app.callback()` runs `configure_logging` before every subcommand,
  reading `[logging]` from config when loadable, else defaulting to
  `INFO json`. Respects `ARCHIVE_AGENT_LOG_LEVEL` env override
- Tests: 11 new (7 logging + 4 audit). The audit tests cover happy
  path, exception re-raise with `outcome=error`, `TimeoutError` →
  `outcome=timeout`, explicit outcome overrides, `fallback` outcome,
  silent operation without conn, and monotonic `latency_ms`. Total
  suite: 77 unit + 4 integration
- Live: 3x `health ollama` → `llm-calls stats` shows 3 rows,
  p50/p95/p99 ≈ 5.5 s (CPU-only qwen2.5:7b on cold cache), all
  outcomes `ok`. `config show` emits `api_key: "**********"` for
  every secret field. `logs tail` on blueridge prints a helpful
  "run this on don-quixote" message since neither `docker` nor
  `journalctl` is on the laptop's PATH

### 2026-04-19 — phase1-05: LLMProvider skeleton + live Ollama round-trip

- `ranking/provider.py` — `LLMProvider` runtime-checkable Protocol +
  `HealthStatus` BaseModel. Contract: never-raise for bad model output
  (fall back instead), every call logs to `llm_calls`
- `ranking/ollama_provider.py` — uses `ollama.AsyncClient` for model
  listing and `instructor.AsyncInstructor` over the OpenAI-compatible
  `/v1/...` endpoint for structured JSON. `health_check` verifies the
  model is pulled, round-trips a 2-field `_SmokeResponse`, logs outcome
  to `llm_calls` regardless of pass/fail. `rank`/`update_profile`/
  `parse_search` raise `NotImplementedError` until phase3
- `ranking/claude_provider.py` — Anthropic client; returns
  status=down cleanly when `ANTHROPIC_API_KEY` is unset (no HTTP call,
  no log row — "never silently fall through to Claude")
- `ranking/tfidf_provider.py` — no external dep; `health_check` always
  ok; other methods `NotImplementedError` until phase3-06
- `ranking/factory.py` — `make_provider(name, cfg)` and
  `make_provider_for_workflow("nightly_ranking", cfg)`. Both take an
  optional `conn` that the providers use for `llm_calls` audit
- CLI: `health ollama` / `health claude` / `health all`. The
  consolidated `health all` gathers Ollama + Jellyfin + Claude (if
  configured) + state DB + disk usage and exits 2 if any component is
  down
- Instructor pitfall documented: `from_provider("ollama/<model>")`
  needs `base_url=http://host:11434/v1` (OpenAI-compat path), not the
  native `/api/*` path. The native `ollama.AsyncClient` uses the base
  URL without `/v1`
- Tests: 10 new (5 factory, 4 llm_calls logging, 1 Ollama live smoke).
  Integration suite is now 4 tests (3 Jellyfin + 1 Ollama smoke),
  all pass under `RUN_INTEGRATION_TESTS=1`. Unit suite: 62 pass
- Added `anthropic.*` to mypy overrides so the pre-commit sandbox
  mypy passes; relaxed Windows-specific
  `PytestUnraisableExceptionWarning` (ProactorEventLoop cleanup
  noise that doesn't affect real test outcome)
- Live `health all` returns clean JSON: ollama ok (5.6 s first-call
  latency incl. cold load), jellyfin 10.11.8, state_db v2, disk 0/500 GB

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
