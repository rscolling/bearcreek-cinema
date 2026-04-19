# SESSION

**Last updated:** 2026-04-19 by phase1-06 logging-observability session (structlog + redaction + audit_llm_call context manager; Phase 1 complete)

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

**Active task:** None. Phase 2 (Archive.org discovery + librarian) is
next: `phase2-01-archive-discovery`, `phase2-02-tmdb-enrichment`,
`phase2-03-tv-grouping`, `phase2-04-ia-get-downloader`,
`phase2-05-librarian-core` … etc. No task cards exist yet for phase 2
beyond the roadmap entries in `TASKS/README.md` — they'll need to be
written before implementation starts.

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

### 2026-04-19 — phase1-04: async Jellyfin client + history ingestion

- `jellyfin/models.py` — 7 Pydantic response models with `extra='ignore'`
  so upstream additions don't break us. Aliases keep snake_case in
  Python, PascalCase on the wire
- `jellyfin/client.py` — `JellyfinClient` as an async context manager
  over `httpx.AsyncClient`. `X-Emby-Token` header auth, 30s timeout,
  paginated item iterator, `ping`, `authenticate`, `get_user`,
  `list_users`, `list_libraries`, `list_items{,_paginated}`, `get_item`,
  `get_user_data`, `trigger_library_scan`, `raw_get` escape hatch
- `jellyfin/history.py` — `MovieWatchRecord` / `EpisodeWatchRecord`
  intermediate models, `classify_movie_signal` implementing the
  bootstrap rules (rewatched/finished/bailed/never-played) with
  `archive_id = "jellyfin:<uuid>"` namespacing, `ingest_all_history`
  that writes both kinds idempotently
- Added **migration 002** (`jellyfin_ingest_dedupe`) — unique index on
  `episode_watches(jellyfin_item_id, timestamp)`. Movie dedupe is
  query-level on `(archive_id, kind, source=bootstrap)`
- CLI: `health jellyfin`, `jellyfin users`, `jellyfin libraries`,
  `history dump [--type movie|show|any] [--since]`, `history sync
  [--dry-run]` — all live against the server
- Tests: 14 new (5 model parsing, 9 history incl. classifier + 3
  idempotence/dry-run round-trips using a fake client over the existing
  `sample_jellyfin_history.json` fixture). 3 live integration tests
  under `RUN_INTEGRATION_TESTS=1` exercise ping, libraries, and user
  resolution. Total suite: 52 unit + 3 integration
- Live run on blueridge → don-quixote: `health jellyfin` OK on
  10.11.8; `history sync` ingested 148 movie taste events (all
  `kind=rejected, strength=0.2` because play_count=0 across the
  library — SESSION.md blocker about cold-start history still holds);
  second sync skipped all 148 — idempotency confirmed
- Added `httpx.*` and `structlog.*` to pyproject `mypy.overrides` so
  the pre-commit sandbox mypy (which only sees declared
  additional_dependencies) doesn't trip on untyped-import errors

### 2026-04-19 — phase1-03: state DB schema + migrations + queries

- `state/models.py` — 11 Pydantic models mirroring CONTRACTS.md §1
  (ContentType/CandidateStatus/TasteEventKind as StrEnum; Candidate,
  TasteEvent, EpisodeWatch, ShowState, EraPreference, TasteProfile,
  RankedCandidate, SearchFilter). TasteEvent validators reject
  content_type=EPISODE and require one of archive_id/show_id
- `state/schema.sql` — DDL for 9 tables with CHECK constraints
  matching the Pydantic invariants (content_type enums, valid zones,
  positive strength/completion, archive_id XOR show_id on taste_events)
- `state/migrations/` — filename-ordered `NNN_*.py` migrations loaded
  via `importlib.util.spec_from_file_location` (filenames start with
  a digit — not valid module names). `apply_pending`, `current_version`,
  `revert_version`, `pending_versions` all public
- `state/db.py` — connection factory (`connect`), singleton
  (`get_db`), `init_db(path, dry_run=)`, `close_db`, `reset_cached_db`.
  Enables WAL mode + foreign_keys on on-disk DBs; :memory: stays
  default for speed
- `state/queries/` — per-entity modules (candidates, taste_events,
  episode_watches, show_state, downloads, llm_calls) with JSON
  round-trip for list columns and ISO-8601 datetimes
- CLI: `archive-agent state init [--dry-run]`, `state info`,
  `state backup <path>`; `state` is the 11th top-level group
- Tests: 24 new (7 candidates, 6 migrations, 7 models, 4 taste_events)
  using an in-memory DB fixture with full migrations applied. Total
  suite: 38 pass
- Verified live on blueridge via an isolated scratch config: `state
  init --dry-run` reports `[1]`, `state init` applies, `state info`
  lists all 9 tables, second `state init` is a no-op, `state backup`
  copies the 100 KB DB file

### 2026-04-19 — phase1-02: typed TOML config + env interpolation

- Wrote `src/archive_agent/config.py`: Pydantic models per CONTRACTS.md
  §5 (Paths, Jellyfin, Archive, Tmdb, Llm{Workflows,Ollama,Claude},
  Librarian{,Tv}, Api, Logging, Config). Secrets wrapped in
  `SecretStr` so `model_dump_json` redacts them automatically
- Loader resolution: explicit path → `ARCHIVE_AGENT_CONFIG` →
  `./config.toml` → `$XDG_CONFIG_HOME/archive-agent/config.toml` →
  `~/.config/archive-agent/config.toml`. Uses stdlib `tomllib`.
  `.env` loaded via `python-dotenv` before interpolation
- Env interpolation: `${VAR}` required, `${VAR:-fallback}` optional
  (matches bash). Missing vars without a fallback raise `ConfigError`
  with the var name AND the TOML path that referenced it
- `validate_config(cfg) -> (warnings, errors)` for cross-field checks
  Pydantic can't express: distinct media paths, DNS-resolvable hosts,
  directories that exist, year-range sanity, claude-selected-but-unset
- CLI wiring: `archive-agent config show` dumps redacted JSON;
  `archive-agent config validate` prints warnings (exit 0) or errors
  (exit 2)
- 12 new unit tests cover happy path, missing-var error, fallback
  syntax, file-not-found listing, explicit-path precedence, secret
  redaction, each validator branch, and interpolation recursion.
  Total: 14 tests pass
- Live check on blueridge: `cp config.example.toml config.toml`,
  `archive-agent config show` prints clean JSON with
  `api_key: "**********"`, `archive-agent config validate` returns
  "Config OK (6 warning(s))" — the 6 warnings are Docker hostnames
  not resolving and `/media/*` not existing (expected outside the
  container)
- Added `dotenv.*` to `pyproject.toml`'s `mypy.overrides` so the
  pre-commit mypy hook (isolated env) doesn't trip on the untyped
  package. Also tweaked `config.example.toml` so the Claude key uses
  the new `${ANTHROPIC_API_KEY:-}` optional syntax

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
