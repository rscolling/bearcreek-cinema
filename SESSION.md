# SESSION

**Last updated:** 2026-04-19 by phase1-02 config session (typed TOML loader + ${VAR:-fallback} env interpolation + validator; 12 new tests pass)

Cross-session continuity for Claude Code working on Bear Creek Cinema.
Read at the start of every session. Updated at the end of every session.
If this file is stale, fix it or delete the stale section — wrong
information here is worse than no information.

This file is *ephemeral operational state only*. Architectural decisions
go to `claude-code-pack/DECISIONS.md`. Bugs go to GitHub issues. Task
progress goes to the checklist in `claude-code-pack/TASKS/README.md`.

---

## Current status

**Phase:** Phase 1 in progress. Scaffold + Ollama stack landed.

**Active task:** None. Next card: `phase1-03-state-schema` (SQLite
schema + migrations) or `phase1-04-jellyfin-client` (REST client +
history ingestion — needs phase1-03 for persistence). `phase1-06`
(logging/observability) can run any time.

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

### 2026-04-19 — phase1-07: Ollama stack live on don-quixote + credentials validated

- Wrote `/home/blueridge/ollama/docker-compose.yml` on the server:
  `ollama/ollama:latest`, CPU-only, named volume for `/root/.ollama`,
  published on `:11434`, `OLLAMA_KEEP_ALIVE=1h`, `ollama list`
  healthcheck. Mirrored into the repo as `docker/ollama.compose.yml`
- `docker compose up -d` succeeded; `ollama_default` network auto-created
- Pulled both models in the background via `nohup`: `llama3.2:3b`
  (31 s) then `qwen2.5:7b` (70 s). Total ~100 s — fast home fiber
- Verified reachability from laptop over Tailscale at
  `http://don-quixote:11434/api/tags` — both models listed with
  full metadata (Q4_K_M GGUF)
- Round-tripped a structured-JSON prompt on `qwen2.5:7b`:
  returned `{"ok": true, "model_name": "qwen2.5"}`, eval_ms=3048,
  total_ms=10904 (includes one-time cold load into RAM)
- TMDb + Jellyfin credentials validated in the same session:
  TMDb key returns HTTP 200 on `/3/configuration`; Jellyfin key +
  user GUID return HTTP 200 on `/Users/{uid}` as `colling`
  (admin). Library has 261 items but ~0 playback — flagged in
  blockers for phase3-04 profile bootstrap
- Ticked `phase1-07` in `TASKS/README.md`; `phase1-02-config` is
  next in line

### 2026-04-18 — phase1-01: scaffold landed

- Wrote `pyproject.toml` (deps + mypy-strict + ruff + pytest config +
  `archive-agent` console script)
- Built the `src/archive_agent/` package tree with Typer CLI stubs for
  all 10 command groups (config, history, discover, download,
  recommend, profile, librarian, serve, daemon, health). Every stub
  prints `not yet implemented` and exits 1
- Added `tests/` with `conftest.py` and two scaffold tests
  (package version, app importable)
- Added `docker/Dockerfile` (python:3.12-slim, non-root UID 1000
  matching `blueridge`), `docker-compose.yml` (joins external
  `jellyfin_default` + `ollama_default`; named volume for state),
  `.dockerignore`
- Added `config.example.toml` per CONTRACTS.md §5
- Added `.pre-commit-config.yaml` with ruff/ruff-format/mypy/pytest-unit
- Validations all green on blueridge: `pytest tests/` (2 pass),
  `mypy --strict src/archive_agent` (clean on 12 files),
  `ruff check` + `ruff format --check` (clean),
  `archive-agent --help` (exit 0), stub subcommand (exit 1),
  `pre-commit run --all-files` (all four hooks pass with venv on PATH)
- On don-quixote: tarball'd source, `docker build` succeeded, `docker
  compose config` validated, container ran `archive-agent --help`
  cleanly; cleaned up scratch image
- Pre-existing drift fixed mid-task: stripped a non-ASCII em-dash and
  arrow from the Typer root help string (Windows cp1252 console
  couldn't encode them)
- Ticked `phase1-01-scaffold.md` in `TASKS/README.md`

### 2026-04-18 — Deployment topology locked in; docs revised

- User decision: archive-agent runs in its own Docker container on
  don-quixote; Ollama runs in its own Docker stack (separate from the
  agent) at `/home/blueridge/ollama/`
- SSH'd into don-quixote (`ssh blueridge@192.168.1.228` — user is
  `blueridge`, not `rob` as `ENVIRONMENT.md` had said). Captured:
  Jellyfin is a Portainer stack with `/media` mounted **ro** into the
  container on `jellyfin_default` network; 31 GB RAM, 821 GB free,
  Intel HD Graphics 530 only (CPU-only Ollama); Ollama not yet
  installed; 28 containers already on the box
- Saved deployment facts to project memory
  (`memory/deployment_topology.md`)
- Revised `ENVIRONMENT.md`: replaced the don-quixote target section
  (host paths vs. container paths, user fix, hardware facts, network
  joins), swapped systemd unit section for a Docker stack section,
  revised "Verifying setup" to exec through `docker compose`
- Updated `phase1-01-scaffold.md`: added Deliverable 6 (Dockerfile,
  docker-compose.yml, .dockerignore) and matching done-when + out-of-scope
- Added new `phase1-07-ollama-stack.md` for standing up the Ollama
  compose + pulling `qwen2.5:7b` and `llama3.2:3b`; registered in
  `TASKS/README.md`. Note: phase1-07 should precede phase1-05 despite
  the number
- Outcome: the Docker deployment shape is now concrete enough that
  `phase1-07` followed by `phase1-01` is a clean forward path. Hardware
  blocker closed. Open: API keys, sibling repo name, `.gitattributes`

### 2026-04-18 — Repo bootstrap: git init + drift reconciliation

- Added root `CLAUDE.md` as a concise entrypoint into
  `claude-code-pack/` (pointers, current state, commands, module
  boundaries)
- `git init -b main`; baseline commit `5be4e03` captures the
  design-complete, pre-code snapshot (43 files)
- Reconciled drift: `TASKS/README.md` had `phase1-01`–`05` ticked as
  done, but no `src/`, `tests/`, or `pyproject.toml` exist on disk.
  Un-ticked those five boxes to match reality; SESSION.md was right,
  the checklist was aspirational
- Outcome: repo is now a real git repo with accurate task-status;
  `phase1-01-scaffold` is unambiguously the next work to pick up.
  Open blockers unchanged (API keys, hardware check, sibling repo
  name, CRLF policy / `.gitattributes` before code lands)

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
