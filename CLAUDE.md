# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read these first, in order

This repo ships its own Claude Code brief. The root file you're reading is a pointer; the authoritative guidance lives in [claude-code-pack/](claude-code-pack/).

1. [SESSION.md](SESSION.md) — current status, blockers, last 5 session entries. **Read at session start, update at session end.** Protocol is at the bottom of the file.
2. [claude-code-pack/CLAUDE.md](claude-code-pack/CLAUDE.md) — standing orders: operating model, communication style, what good looks like.
3. [claude-code-pack/GUARDRAILS.md](claude-code-pack/GUARDRAILS.md) — hard rules that override task cards and user requests (data safety, disk budget, module boundaries, secrets).
4. [claude-code-pack/CONTRACTS.md](claude-code-pack/CONTRACTS.md) — frozen Pydantic schemas, HTTP/CLI signatures, config shape, DB schema. Changes require an ADR.
5. [claude-code-pack/DECISIONS.md](claude-code-pack/DECISIONS.md) — ADR-001 through ADR-012. Don't relitigate.
6. [claude-code-pack/TASKS/](claude-code-pack/TASKS/) — unit of work. Pick a card; don't start work outside a card.
7. [claude-code-pack/TESTING.md](claude-code-pack/TESTING.md), [claude-code-pack/ENVIRONMENT.md](claude-code-pack/ENVIRONMENT.md) — consult as needed.

Narrative reference (for humans and for non-trivial design decisions): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Current repo state (as of the last SESSION.md update)

**Design-complete, pre-code.** There is no `src/`, no `pyproject.toml`, no `tests/` directory yet. Running `bash claude-code-pack/scripts/bootstrap-dev.sh` fails at `pip install -e .` — this is expected until [phase1-01-scaffold.md](claude-code-pack/TASKS/phase1-01-scaffold.md) runs. Verify against `SESSION.md` before trusting this paragraph.

## Commands (once scaffold exists)

```bash
# One-time dev setup
bash claude-code-pack/scripts/bootstrap-dev.sh

# Tests
pytest tests/unit/                                    # fast, run on every commit
pytest tests/unit/ --cov=archive_agent --cov-report=term-missing
RUN_INTEGRATION_TESTS=1 pytest tests/integration/     # hits real Ollama/Jellyfin
pytest tests/unit/test_librarian.py -v                # single file
pytest tests/unit/ -k librarian                       # by keyword

# Type check (must pass --strict on all new code)
mypy --strict src/archive_agent

# Lint / format
ruff check src/ tests/
ruff format src/ tests/

# CLI smoke tests
archive-agent config validate
archive-agent state init
archive-agent health all            # expects ollama + jellyfin + state_db + disk all "ok"
```

Pre-commit runs `ruff check`, `ruff format --check`, `mypy --strict`, and `pytest tests/unit/ -x --ff`; commits are blocked on failure. Install once per clone with `pre-commit install`.

## Architecture: the big picture

A Python async daemon on `don-quixote` (Ubuntu home server) that curates public-domain movies and TV from Archive.org into a Jellyfin library, driven by a companion Roku app. Two systemd user units: `archive-agent-daemon` (async job loop) and `archive-agent-api` (FastAPI HTTP for the Roku app).

**Module boundaries are load-bearing** (enforced by [GUARDRAILS.md](claude-code-pack/GUARDRAILS.md#code-boundaries) — don't short-circuit them):

- `state/` — **owns the SQLite DB.** No ad-hoc `sqlite3.connect()` elsewhere. All queries are functions in this module.
- `jellyfin/` — owns all Jellyfin REST I/O.
- `archive/` — owns all Archive.org I/O (via `ia-get` + `internetarchive`).
- `librarian/` — owns all filesystem writes under `/media/`. Other modules *ask* it to place a file; they never `shutil.move` themselves.
- `ranking/` — owns the `LLMProvider` protocol and its three implementations: `OllamaProvider` (default), `ClaudeProvider` (optional premium), `TFIDFProvider` (fallback when Ollama is down). Fallback order is Ollama → TF-IDF, **never** silently to Claude.
- `taste/` — unified household profile. Movie events go in directly; **episode events do not touch the profile** — they update per-show resume state, which the show-state aggregator then converts to `binge_positive` / `binge_negative` events (see ARCHITECTURE.md §"Unified taste profile").
- `api/` — FastAPI surface the Roku app hits. Contract in [CONTRACTS.md](claude-code-pack/CONTRACTS.md).
- `loop.py` — the async scheduler that runs discovery, ranking, aggregation, and librarian passes.

**Two-stage ranking:** TF-IDF cosine prefilter trims O(10⁴) candidates to ~50; then the LLM reranks to a 5-10 shortlist with reasoning. No vector DB — `scikit-learn` in-memory is the right size ([ADR-009](claude-code-pack/DECISIONS.md)).

**Disk as a real subsystem — the librarian** manages four zones with distinct policies:

| Zone | Eviction | Notes |
|---|---|---|
| `/media/movies` | **Never auto-evicted** | User-owned; the librarian must hard-filter this out |
| `/media/tv` (committed) | Slow, audited, with grace period | Destructive action must log to `librarian_actions` first |
| `/media/recommendations` | 14 days untouched | Evict oldest first when over budget |
| `/media/tv-sampler` | 30 days untouched | 3-episode samples; promote to `/media/tv` after 2 finishes |

`max_disk_gb` is a hard cap on the agent-managed zones, not a warning.

**LLM stack:** Local-first Ollama (`qwen2.5:7b` for ranking/profile, `llama3.2:3b` for NL search parsing). Prompts must fit in context with margin — use `archive_agent.testing.token_budget.check_prompt_fits`. Claude API is opt-in per workflow; never fall through to it silently.

## Data model invariants

`ContentType` (MOVIE/SHOW/EPISODE) is first-class everywhere — in the DB, in the TF-IDF features, in the HTTP filters. `TasteEvent.content_type` is only ever MOVIE or SHOW — **never EPISODE** (see ARCHITECTURE.md §"Unified taste profile").

`Candidate.status` follows: `new → ranked → approved → (sampling) → downloading → downloaded → committed`, with `rejected` / `expired` as terminal states. Enforced in [CONTRACTS.md](claude-code-pack/CONTRACTS.md).

State DB migrations are reversible — every `up` needs a `down` in the same commit.

## Working style conventions (from claude-code-pack/CLAUDE.md)

- Commit messages: `[phase1-04] jellyfin client: auth + history fetch` — one task card per commit.
- State the task card ID on the first line of a response when starting work.
- If a task card is ambiguous, stop and ask — don't guess.
- When a task is done, confirm each "done when" bullet with the command or test that verifies it.
- `mypy --strict` on new code. Typed Pydantic models throughout.
- `structlog` for logs. No bare `print()` in library code. Secrets are auto-redacted by key name (`api_key`, `token`, `password`, `secret`) — don't work around the redactor.
- Resource-holding objects (HTTP clients, DB connections) use async context managers or explicit `.close()`.

## Two hosts to keep straight

- **`don-quixote`** — Ubuntu home server. Production target. Runs Jellyfin, Ollama, the agent daemon, and the API.
- **`blueridge`** — Windows laptop. Dev workstation with VS Code + Claude Code. Reaches `don-quixote` over Tailscale. Use `archive-agent daemon --dev` so media paths go to `./dev-media/` instead of the unreachable `/media/*`.
