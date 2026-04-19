# bearcreek-cinema — file inventory

Unzip `bearcreek-cinema-repo.zip` into a folder, `cd bearcreek-cinema/`, and
that's your Claude Code project root. Everything below is what's in there
and what each file is doing.

Total: 41 files (+ 3 directories that Claude Code will populate during
`phase1-01-scaffold`: `src/`, `tests/`, `systemd/`).

---

## Tier 1 — Files Claude Code reads every session

These are the core operating context. Claude Code reads `SESSION.md` and
`CLAUDE.md` at session start, then consults the others as work demands.

| Path | Role |
|---|---|
| `SESSION.md` | Cross-session state. Current status, blockers, last 5 session entries. Updated at end of every session. |
| `claude-code-pack/CLAUDE.md` | Standing orders for Claude Code. Operating model, communication style, what good looks like. |
| `claude-code-pack/CONTRACTS.md` | Frozen interfaces — Pydantic schemas, HTTP endpoints, CLI signatures, config TOML shape, DB schema. Changes require ADR. |
| `claude-code-pack/GUARDRAILS.md` | Hard rules. "Never modify Jellyfin's DB," "never exceed disk budget," secrets hygiene, code boundaries. Overrides everything else. |
| `claude-code-pack/DECISIONS.md` | Architectural Decision Records (ADR-001 through ADR-012). Decisions already made; don't relitigate without a new ADR. |
| `claude-code-pack/TESTING.md` | Test strategy, fixture locations, coverage expectations, manual verification protocol. |
| `claude-code-pack/ENVIRONMENT.md` | Dev setup for `don-quixote` and `blueridge`, environment variables, pre-commit hooks, systemd units, verification commands. |

## Tier 2 — Work queue

Individual units of executable work. Claude Code picks a task card,
implements it against the contracts, verifies done-when criteria.

| Path | Role |
|---|---|
| `claude-code-pack/TASKS/README.md` | Roadmap across all phases. Checklist of done/not-done tasks. |
| `claude-code-pack/TASKS/phase1-01-scaffold.md` | Repository scaffold: pyproject.toml, src/archive_agent/ tree, stub CLI, tests directory, pre-commit config. |
| `claude-code-pack/TASKS/phase1-02-config.md` | Typed TOML config loading with env variable interpolation. |
| `claude-code-pack/TASKS/phase1-03-state-schema.md` | SQLite schema + hand-rolled migration system, DDL, query modules per entity. |
| `claude-code-pack/TASKS/phase1-04-jellyfin-client.md` | Async Jellyfin REST client, watch history ingestion for movies and episodes. |
| `claude-code-pack/TASKS/phase1-05-ollama-smoke.md` | Ollama health check, `LLMProvider` protocol, three stub implementations (Ollama, Claude, TF-IDF). |
| `claude-code-pack/TASKS/phase1-06-logging-observability.md` | structlog config, secret redaction, `llm_calls` audit context manager. |
| `claude-code-pack/TASKS/phase3-09-fts5-indexing.md` | SQLite FTS5 virtual table with trigram tokenizer for catalog search. |
| `claude-code-pack/TASKS/phase4-08-query-router.md` | Query intent classification + `/search`, `/search/similar`, `/search/autocomplete` endpoints. |
| `claude-code-pack/TASKS/phase5-07-roku-voice-search.md` | Voice search scene in the Roku app using `VoiceTextEditBox`. |

Phases 2, 3 (other cards), 4 (other cards), 5 (other cards), 6 are listed
in `TASKS/README.md` but don't have full cards yet. Claude Code or you will
write them as those phases come up.

## Tier 3 — Reference material

Claude Code reads these when they're relevant to a task, not on every
session.

| Path | Role |
|---|---|
| `docs/ARCHITECTURE.md` | Full system design. The authoritative narrative reference. |
| `docs/curator.md` | One-page summary of the running agent: keep current, recommend, search. |
| `docs/search-and-retrieval.md` | Search subsystem design: three retrieval jobs, indexing strategy, query router. |
| `docs/design-principles.md` | The stance behind the design. Local-first, graceful degradation, right-sized infrastructure, etc. |
| `docs/case-study.md` | Portfolio narrative for outside readers. Includes "When I would have used a vector database" section. |

## Tier 4 — Fixtures and scripts

Test data and bootstrap scripts.

| Path | Role |
|---|---|
| `claude-code-pack/fixtures/sample_jellyfin_history.json` | Realistic Jellyfin `/Users/{id}/Items` response with mixed movie and episode playback. |
| `claude-code-pack/fixtures/sample_archive_search.json` | Paginated Archive.org search result covering movies, single-episode TV, full-season TV, and an ambiguous item. |
| `claude-code-pack/fixtures/sample_ollama_rank_response.json` | What a healthy structured ranking response looks like. |
| `claude-code-pack/fixtures/sample_taste_profile.json` | Realistic populated taste profile. |
| `claude-code-pack/scripts/bootstrap-dev.sh` | Dev environment setup: venv, dependencies, .env, Ollama check. |

## Tier 5 — Public repo files

For humans landing on GitHub. Claude Code generally doesn't modify
these except when they reference code structure or commands.

| Path | Role |
|---|---|
| `README.md` | Front door. What the project is, status, features, how to start. |
| `LICENSE` | MIT. |
| `CHANGELOG.md` | Release notes (currently just Unreleased and 0.1.0 planned). |
| `CONTRIBUTING.md` | Contribution guidelines, PR standards, what gets rejected. |
| `CODE_OF_CONDUCT.md` | Short, direct. |
| `SECURITY.md` | How to report vulnerabilities privately, what counts and doesn't. |
| `.gitignore` | Load-bearing for first-commit secret safety. Never touch unless adding a legitimate pattern. |
| `.env.example` | Template for `.env`. Copy this, fill in real keys, never commit the real file. |

## Tier 6 — GitHub integration

| Path | Role |
|---|---|
| `.github/ISSUE_TEMPLATE/bug_report.md` | Bug report form. |
| `.github/ISSUE_TEMPLATE/proposal.md` | Feature proposal form that routes through the ADR process. |
| `.github/pull_request_template.md` | PR template pointing at task cards and done-when criteria. |
| `.github/workflows/ci.yml` | GitHub Actions: ruff lint + format check, mypy strict, pytest unit, across Python 3.11 and 3.12. |

## Tier 7 — Portfolio (not for Claude Code, for you)

These are not read by Claude Code. They're for your LinkedIn, résumé,
and Agentic Agent consulting materials. Kept in the repo because they're
versioned alongside the work they describe.

| Path | Role |
|---|---|
| `portfolio/cv-bullets.md` | CV bullets at multiple lengths, LinkedIn Projects section, Agentic Agent landing page copy, positioning notes. Updated with paired-project (BCC + claude-docs-rag) framing. |
| `portfolio/consulting-pitch.md` | Translation from personal project to client engagement: scope, templates, pricing framing. |

---

## What's not in the zip (generated by Claude Code)

These get created during Phase 1 and later:

- `pyproject.toml` — created by `phase1-01-scaffold`
- `src/archive_agent/` tree — created across Phase 1 cards
- `tests/` tree — created alongside source
- `config.example.toml` — created by `phase1-02-config`
- `.pre-commit-config.yaml` — created by `phase1-01-scaffold`
- `roku/bear-creek-cinema/` tree — created during Phase 5
- `systemd/` unit files — created during Phase 6

When you unzip, the directory is intentionally light on code — this is
pre-Phase-1 state. The zip is the specification; Claude Code turns it
into a codebase.

---

## First steps after unzipping

```bash
unzip bearcreek-cinema-repo.zip
cd bearcreek-cinema
git init
git add .
git commit -m "Initial: pre-Phase-1 specification and scaffolding"
git remote add origin git@github.com:<you>/bearcreek-cinema.git
git branch -M main
git push -u origin main
```

Then:

1. Edit `.env.example` values into a real `.env` (JELLYFIN_API_KEY,
   TMDB_API_KEY — `.env` is gitignored)
2. Open the folder in Claude Code
3. Claude Code should read `SESSION.md` first (its instructions tell it
   to); verify this happens
4. Point Claude Code at `claude-code-pack/TASKS/phase1-01-scaffold.md`
   as the first task
5. When it finishes, confirm the checklist items and that `SESSION.md`
   got updated with a new "Recent sessions" entry

---

## A few things to double-check before first public push

The README, case study, consulting pitch, and SECURITY.md all contain
placeholder text:

- `github.com/<you>/bearcreek-cinema` — replace with your actual GitHub
  URL
- `<you>` in acknowledgments — your GitHub handle or full name
- `agenticagent.example` — your real Agentic Agent domain when live
- `security@agenticagent.example` in SECURITY.md — your real security
  contact email
- `Copyright (c) 2026 Rob Ross` in LICENSE — verify this is the
  attribution you want

Quick find-and-replace before the first commit is cleaner than fixing
them later. A one-liner:

```bash
grep -rl "<you>" . --include="*.md" | xargs sed -i 's|<you>|yourhandle|g'
grep -rl "agenticagent.example" . --include="*.md" | xargs sed -i 's|agenticagent.example|agenticagent.com|g'
```

Adjust the replacements to the actual values. Do this once, review the
diff, then commit.
