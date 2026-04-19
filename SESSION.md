# SESSION

**Last updated:** 2026-04-18 by design session (not a code-writing session)

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

**Active task:** None. Ready to start `phase1-01-scaffold.md` when a
coding session opens.

**Codebase state:** No `src/` yet. Repo contains documentation,
task cards, design docs, and portfolio materials. A fresh
`bash claude-code-pack/scripts/bootstrap-dev.sh` would fail on the
`pip install -e .` step because there's no `pyproject.toml` yet —
this is expected and is the first deliverable of `phase1-01`.

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
- **Hardware check:** `free -h`, `lspci | grep -i vga`, `df -h` output
  from `don-quixote` not yet captured — needed before finalizing
  Ollama model choice for `phase1-05`

---

## Recent sessions

*Most recent first. Prune entries older than the last 5 retained.*

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

### 2026-04-18 — Repo initialization

- Created Bear Creek Cinema repo structure with README, LICENSE,
  CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, CHANGELOG
- Migrated `claude-code-pack/` from earlier `archive-agent/` working
  location
- Added GitHub templates (bug report, proposal, PR) and CI workflow
- Outcome: public-ready repo skeleton; still needs `pyproject.toml`
  and `src/` when `phase1-01` runs

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
