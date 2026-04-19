# Archive.org → Jellyfin Recommendation Agent

You are working on a self-hosted agent that curates public-domain movies and
TV from Archive.org, learns household taste from Jellyfin playback, and
surfaces recommendations via a custom Roku app. The full system design is in
`ARCHITECTURE.md`. Read it before making non-trivial decisions.

## Your operating model

1. **Start every session by reading `SESSION.md` at the repo root.**
   It has current state, blockers, and recent-session context. If it's
   stale, fix it or flag the drift before starting work. End every
   session by updating it. See the protocol section at the bottom of
   `SESSION.md`.

2. **Work from task cards in `TASKS/`.** Each card is a unit of work
   with explicit "done when" criteria. Don't start new work that isn't
   captured in a task card; if needed, propose a new card first.

3. **Respect frozen contracts in `CONTRACTS.md`.** Schemas, API
   signatures, and CLI shapes are decided. Changing them breaks
   downstream work. Propose a change in a comment rather than silently
   editing.

4. **Honor `GUARDRAILS.md`.** These are hard rules. "Never modify
   Jellyfin's own database," "never exceed the configured disk
   budget," etc.

5. **Don't re-debate things in `DECISIONS.md`.** They were decided for
   reasons. If a decision feels wrong, surface it in a comment; don't
   silently relitigate.

6. **Test as you go.** See `TESTING.md`. A task isn't done until its
   tests pass.

## Project context

- **Language:** Python 3.11+, typed with Pydantic and type hints throughout.
- **LLM:** Ollama local-first (`qwen2.5:7b` default), Claude API optional,
  TF-IDF fallback.
- **Content:** Both movies (`archive.org/details/moviesandfilms`) and TV
  (`archive.org/details/television`), unified taste profile.
- **Target deployment:** `don-quixote` (Ubuntu home server). Dev often on
  `blueridge` (Windows laptop) over Tailscale.
- **User:** Rob runs the household. Agent serves Rob + partner via one
  shared Jellyfin account (single-account limitation is a known design
  constraint; see ARCHITECTURE.md).

## Repository layout

```
archive-agent/
├── pyproject.toml
├── README.md
├── config.example.toml
├── src/archive_agent/
│   ├── __init__.py
│   ├── __main__.py               # CLI entry
│   ├── config.py
│   ├── state/                    # SQLite schema + queries
│   ├── archive/                  # Archive.org discovery + download
│   ├── jellyfin/                 # Jellyfin REST client
│   ├── taste/                    # Profile + bootstrap + updates
│   ├── ranking/                  # LLM provider + TF-IDF + reranker
│   ├── librarian/                # Disk budget, zones, eviction
│   ├── api/                      # FastAPI HTTP service for Roku
│   ├── metadata/                 # TMDb
│   └── loop.py                   # Main async scheduler
├── tests/
│   ├── unit/
│   ├── integration/              # Hit real services (optional fixtures)
│   └── fixtures/
├── roku/bear-creek-cinema/       # BrightScript app (later phase)
├── scripts/                      # Operational scripts
└── systemd/                      # Unit files for deployment
```

## Communication style

- When starting a task, state the card you're working on in the first line
  of your response.
- If a task card is ambiguous, stop and ask rather than guess.
- When you finish a task, summarize what changed and confirm each "done
  when" bullet was satisfied, with the command or test that verifies it.
- Keep commits scoped to one task card each; commit message format:
  `[phase1-04] jellyfin client: auth + history fetch`.
- **Before ending a session, update `SESSION.md`.** Timestamp, current
  status, blockers, and a new "Recent sessions" entry with outcome.
  Don't invent completion; if a task is partial, say so.

## What good looks like

- Code is typed. `mypy --strict` passes on new code.
- Tests accompany every non-trivial function. Target ~80% coverage; don't
  chase 100%.
- No commented-out code in commits. Delete or leave it.
- Module docstrings explain *why*, not *what*. Function docstrings state
  the contract.
- Errors are logged with `structlog` at the right level; no bare `print()`
  in library code.
- Resource-holding objects (HTTP clients, DB connections) use async context
  managers or have explicit `.close()`.

## What you should not do

- Don't invent new abstractions that aren't in the architecture. If it
  needs a new subsystem, propose it as an ADR (see `DECISIONS.md`
  format) first.
- Don't pull in new heavyweight dependencies without proposing them.
  Stack choices are in `ARCHITECTURE.md`; additions need a reason.
- Don't stub out "TODO" functions and claim the task done. If you can't
  implement it, say so.
- Don't hardcode secrets. Use environment variables, `.env` for dev (and
  ensure `.env` is gitignored).
- Don't reformat existing code you didn't otherwise touch. Noise hurts
  review.
