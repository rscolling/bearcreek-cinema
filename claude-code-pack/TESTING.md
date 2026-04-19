# Testing Strategy

A task is not done until its tests pass. This document defines what
"tests pass" means for different kinds of work.

---

## Tiers

**Unit tests** (`tests/unit/`):
- Pure logic, no I/O
- Run in <5 seconds total
- No mocks of internal code — if something's hard to test, restructure it
- Run on every commit: `pytest tests/unit/`

**Integration tests** (`tests/integration/`):
- Hit real services (Ollama, Jellyfin, maybe Archive.org)
- Marked with `@pytest.mark.integration`
- Skipped by default; enabled with `RUN_INTEGRATION_TESTS=1 pytest`
- Must tear down any state they create

**End-to-end tests** (`tests/e2e/`, deferred to Phase 4+):
- Full pipeline: discovery → rank → serve via HTTP
- Marked `@pytest.mark.e2e`
- Run manually before a release

---

## What to test

For each module, at minimum:

- **Happy path** — one or two tests showing intended use
- **Boundary cases** — empty inputs, max sizes, malformed data
- **Error paths** — what happens when the DB is locked, when Ollama times
  out, when Archive.org returns 404

For `LLMProvider` implementations specifically:

- A test that malformed JSON from the model is caught and retried
- A test that all-retries-failed falls through to the fallback ranker
- A test that `update_profile` preserves liked/disliked IDs even if the
  model omits them

For the librarian:

- A test that a download exceeding budget is rejected
- A test that eviction order respects TTLs
- A test that `/media/movies` is never evicted

---

## Fixtures

Shared fixtures live in `tests/fixtures/` and `claude-code-pack/fixtures/`.
Available fixtures to import:

- `sample_jellyfin_history.json` — a Jellyfin `/Users/{id}/Items` response
  with mixed movie and episode playback
- `sample_archive_search.json` — a paginated Archive.org search result
- `sample_ollama_rank_response.json` — what a healthy ranking response looks
  like
- `sample_taste_profile.json` — a realistic populated profile

Use them via `tests/conftest.py` fixtures named `jellyfin_history`,
`archive_search`, etc.

---

## Test commands

```bash
# Unit only (fast, on every commit)
pytest tests/unit/

# With coverage
pytest tests/unit/ --cov=archive_agent --cov-report=term-missing

# Integration (requires services running)
RUN_INTEGRATION_TESTS=1 pytest tests/integration/

# Specific file
pytest tests/unit/test_librarian.py -v

# Only changed files (requires pytest-testmon or manual)
pytest tests/unit/ -k librarian
```

---

## Type checking

Every module must pass `mypy --strict`. Run before claiming a task done:

```bash
mypy --strict src/archive_agent
```

If mypy complains about a third-party library without types, add an ignore
in `pyproject.toml` under `[[tool.mypy.overrides]]`, not in-line.

---

## Lint / format

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Lint must be clean. Format must be applied.

---

## Token budget testing (LLM prompts)

For every prompt sent to Ollama, verify it fits. Utility:
`archive_agent.testing.token_budget`.

```python
from archive_agent.testing.token_budget import check_prompt_fits

def test_rank_prompt_fits_qwen_7b():
    prompt = build_rank_prompt(profile, candidates[:50])
    assert check_prompt_fits(prompt, model="qwen2.5:7b", margin_tokens=2000)
```

Budget test runs on every LLM prompt template.

---

## Manual verification checklist (for task cards)

When a task card's "done when" includes behavioral checks, you run them
and show the output. Example for task `phase1-04-jellyfin-client`:

```
✓ Can authenticate: archive-agent jellyfin ping
  → {"status": "ok", "server_version": "10.9.8"}
✓ Can list users: archive-agent jellyfin users
  → prints at least 1 user
✓ Can fetch history: archive-agent history dump --type movie | head -5
  → prints at least one movie with completion %
```

Paste the actual command output into the task completion message.

---

## Fixtures: how to add a new one

1. Run the real command once, capture output:
   `archive-agent jellyfin raw /Users/{id}/Items > tests/fixtures/new_fixture.json`
2. Redact personal info: user IDs → `user-uuid-1`, API keys → `REDACTED`
3. Trim to smallest size that still demonstrates the case
4. Add a conftest.py fixture that loads it
5. Reference it in your test

Never check in fixtures with real API keys, device IDs, or other secrets.
