# SESSION

**Last updated:** 2026-04-18 by deployment-topology session (Docker decision locked in; ENVIRONMENT.md + phase1-01 revised; phase1-07 Ollama card added)

Cross-session continuity for Claude Code working on Bear Creek Cinema.
Read at the start of every session. Updated at the end of every session.
If this file is stale, fix it or delete the stale section — wrong
information here is worse than no information.

This file is *ephemeral operational state only*. Architectural decisions
go to `claude-code-pack/DECISIONS.md`. Bugs go to GitHub issues. Task
progress goes to the checklist in `claude-code-pack/TASKS/README.md`.

---

## Current status

**Phase:** Design complete. No code written yet.

**Active task:** None. Two cards ready; `phase1-07-ollama-stack.md`
should run **before** `phase1-01-scaffold.md` so the scaffold's
container build has a real Ollama endpoint to point at. (They're
independent for *completion*, but running 07 first means `health all`
works on the first try.)

**Codebase state:** Git initialized on `main`; baseline commit `5be4e03`
captures the design-complete, pre-code state (43 files). No `src/`,
`tests/`, `pyproject.toml`, or `config.example.toml` yet. A fresh
`bash claude-code-pack/scripts/bootstrap-dev.sh` would fail on the
`pip install -e .` step because there's no `pyproject.toml` yet —
this is expected and is the first deliverable of `phase1-01`.
A root `CLAUDE.md` was added as a concise pointer into
`claude-code-pack/`.

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

### 2026-04-18 — Design round: search and retrieval subsystem

- Created `docs/search-and-retrieval.md` covering catalog / intent /
  recommendation jobs as three distinct retrieval problems
- Added task cards: `phase3-09-fts5-indexing`, `phase4-08-query-router`,
  `phase5-07-roku-voice-search`
- Added ADR-012 ("SQLite FTS5 + in-memory TF-IDF, no vector DB") to
  `DECISIONS.md`
- Updated `CONTRACTS.md` with new search endpoints and
  `SearchResultItem` model
- Outcome: search subsystem specified, three task cards ready for
  Claude Code

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
