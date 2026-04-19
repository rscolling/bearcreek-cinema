# Guardrails

These are hard rules. Violating them is a bug regardless of what a task
card or user request says. If a rule seems to block a legitimate request,
stop and ask.

---

## Data safety

**Never modify Jellyfin's own database.** The agent reads from Jellyfin's
REST API and writes to its own SQLite. Direct writes to Jellyfin's DB are
forbidden.

**Never move or delete files in `/media/movies` without explicit user
opt-in.** That zone is user-curated. The agent reads it (to check what's
already owned) but does not evict from it. The librarian's eviction logic
must hard-filter out `/media/movies`.

**Never delete a committed `/media/tv` show without writing the intent to
the `librarian_actions` audit table first and waiting a configurable grace
period.** Committed TV is expensive to re-download.

**State DB migrations must be reversible.** If you add an `up` migration,
write the `down` migration in the same commit.

---

## Resource safety

**Respect `librarian.max_disk_gb`.** Downloads must check remaining budget
before starting. Exceeding the budget is a bug, not a warning.

**Respect `max_concurrent_downloads`.** Don't spawn bypass workers. Use
the shared semaphore.

**Never saturate Archive.org.** Honor `Retry-After` headers. Back off
exponentially on 429/503. If the library `internetarchive` or `ia-get`
enforces its own throttling, don't add another layer that subverts it.

---

## Secrets

**Never commit secrets.** `.env` is gitignored. API keys come from
environment variables or `.env` files only. Config files use the
`${ENV_VAR}` interpolation syntax; they are safe to commit.

**Never log secrets.** The logging layer has a redaction allowlist (see
`archive_agent.logging.redact`). Keys named `api_key`, `token`,
`password`, `secret` are redacted automatically. Don't work around this.

---

## External services

**Archive.org is a free public resource. Treat it accordingly.** No parallel
download bombs. No repeated polling. Cache metadata when possible. If in
doubt, slow down.

**TMDb has rate limits.** The metadata module caches aggressively and uses
backoff. Don't bypass the cache.

**Ollama runs on `don-quixote`'s hardware.** Don't assume infinite context
or free compute. Every prompt should fit comfortably in the model's
context window with margin. Budget prompts; see `TESTING.md` for token
counting utilities.

**Claude API costs money.** Use it only when a workflow is explicitly
configured to use it. Don't silently fall through to Claude when Ollama
fails; fall through to TF-IDF instead (see LLMProvider contract).

---

## Code boundaries

**The `state/` module owns the database.** Other modules talk to the DB
only through functions defined in `state/`. No ad-hoc `sqlite3.connect()`
calls elsewhere.

**The `jellyfin/` module owns Jellyfin I/O.** Other modules call its
functions; they do not make HTTP calls to Jellyfin directly.

**The `archive/` module owns Archive.org I/O.** Same principle.

**The `librarian/` module owns filesystem writes under `/media/`.** Other
modules ask the librarian to place a file; they do not `shutil.move` on
their own.

This layering exists so we can add mocking, add metrics, and test each
layer independently. Don't short-circuit it.

---

## Testing

**Tests may not require live network.** Integration tests that hit real
Ollama, Jellyfin, or Archive.org are OK but must be marked with
`@pytest.mark.integration` and skipped in CI unless
`RUN_INTEGRATION_TESTS=1`.

**Tests may not leave state on the host.** Use tmp directories. Never
write to `/media/*` or `/var/lib/archive-agent/*` from a test.

**Fixtures are in `tests/fixtures/` (or `claude-code-pack/fixtures/` for
shared examples).** Don't inline large blobs in test files.

---

## Dependencies

**Don't add dependencies not listed in `ARCHITECTURE.md`'s Stack table
without proposing them as an ADR.** "I needed something to parse dates"
is not a proposal; explain why stdlib or an existing dep won't work.

**Prefer stdlib, then the dependencies already listed, then new
dependencies.** In that order.

---

## User-facing behavior

**The agent must never "surprise delete" content.** Any destructive
action on content the user has interacted with must be:
1. Logged to `librarian_actions` before execution
2. Subject to a grace period if it's committed content
3. Reflected in `/disk` API response so the UI can surface it

**The agent must never promote itself above the user's intent.** If
config says `max_disk_gb = 100`, respect it. If the user rejects a
recommendation, never re-recommend the same item without a clear
intervening positive signal.

**The Roku app is a thin client.** All ranking, filtering, and
decision-making happens on `don-quixote`. The Roku app renders what
the API gives it.
