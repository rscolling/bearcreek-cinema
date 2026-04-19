# SESSION

**Last updated:** 2026-04-18 by phase1-01 scaffold session (Python package + CLI stubs + Dockerfile + compose — all validations green)

Cross-session continuity for Claude Code working on Bear Creek Cinema.
Read at the start of every session. Updated at the end of every session.
If this file is stale, fix it or delete the stale section — wrong
information here is worse than no information.

This file is *ephemeral operational state only*. Architectural decisions
go to `claude-code-pack/DECISIONS.md`. Bugs go to GitHub issues. Task
progress goes to the checklist in `claude-code-pack/TASKS/README.md`.

---

## Current status

**Phase:** Phase 1 in progress. Scaffold landed.

**Active task:** None. `phase1-01-scaffold` is done (commits follow
this SESSION update). Next cards: `phase1-07-ollama-stack` (stand up
Ollama on don-quixote) and `phase1-02-config` (parallel — either
ordering works).

**Codebase state:** Python package live at `src/archive_agent/` with
stub CLI (10 command groups, all exit 1), `tests/` scaffold with two
smoke tests, `docker/Dockerfile` + `docker-compose.yml` + `.dockerignore`
for the prod target. `pyproject.toml` declares deps + mypy-strict +
ruff. Pre-commit config covers ruff/ruff-format/mypy/pytest-unit.
`.venv/` on blueridge has the package installed editable with dev
extras. Scaffold image built successfully on don-quixote (`docker
build` + `docker compose config` both validated in a scratch dir,
then cleaned up).

**Ollama status:** Not yet installed on `don-quixote`. Installation and
`qwen2.5:7b` pull are prerequisites for `phase1-05`; documented in
`claude-code-pack/ENVIRONMENT.md`.

**Jellyfin status:** Running on `don-quixote` with existing watch
history. API key not yet provisioned for the agent's use.

---

## Blockers / waiting on

- **User task:** provision a Jellyfin API key and TMDb API key, fill
  them into `.env` once the scaffold lands
- **User decision:** final repo name for the sibling RAG project
  (`claude-docs-rag` is the working name, alternatives discussed)
- **`.gitattributes`** not yet added — CRLF/LF policy should be set
  before Python/BrightScript files land in phase1-01

---

## Recent sessions

*Most recent first. Prune entries older than the last 5 retained.*

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

### 2026-04-18 — Design round: heartbeat / SESSION.md

- Discussed two-agent separation (Claude Code vs. running Bear Creek
  Cinema agent) and clarified scope
- Added `SESSION.md` at repo root with Option 3 structure (current
  state + recent log)
- Updated `claude-code-pack/CLAUDE.md` to reference SESSION.md in the
  operating model
- Outcome: SESSION.md seeded, protocol documented, no code changes

### 2026-04-18 — Design round: paired-project portfolio positioning

- Added "When I would have used a vector database" section to
  `docs/case-study.md`
- Designed `claude-docs-rag` sibling project (docs/DESIGN.md,
  README.md)
- Updated `portfolio/cv-bullets.md` with paired-project framing for
  resume, LinkedIn, and Agentic Agent landing page
- Outcome: portfolio story lands both vector-DB and no-vector-DB
  signals honestly

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
