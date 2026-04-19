# Environment Setup

Two dev/deploy targets: `don-quixote` (Ubuntu, primary deployment) and
`blueridge` (Windows laptop, dev over Tailscale or local).

---

## Quick start

From a fresh checkout on either machine:

```bash
bash scripts/bootstrap-dev.sh
```

That script should leave you with:
- Python 3.11+ venv at `.venv/`
- Dependencies installed
- `.env` created from `.env.example` (you fill in API keys)
- SQLite state DB initialized at `/tmp/archive-agent-dev/state.db`
- A smoke-test command printing "OK" for each subsystem

If bootstrap fails, fix the bootstrap rather than working around it.

---

## Target: don-quixote (Ubuntu)

Deployment host. Runs:

- Jellyfin (already installed, port 8096)
- Ollama (install below)
- The archive-agent daemon
- The archive-agent HTTP API
- Ideally, nothing else compute-heavy

**Directory conventions:**

```
/var/lib/archive-agent/        # state DB, logs
/etc/archive-agent/            # config.toml (production)
/opt/archive-agent/            # venv + source (or wherever you install)
/media/movies/                 # user-owned, never auto-evicted
/media/tv/                     # committed TV shows
/media/recommendations/        # agent-managed, evictable
/media/tv-sampler/             # agent-managed, evictable
```

**User:** create a `rob` service user (already exists). Agent runs as
that user via systemd `--user` service.

**Ollama install (Ubuntu):**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b
ollama pull llama3.2:3b
systemctl --user enable ollama
systemctl --user start ollama
```

Verify:
```bash
curl http://localhost:11434/api/tags
ollama run qwen2.5:7b "Return the JSON {\"ok\": true}" --format json
```

---

## Target: blueridge (Windows laptop)

Dev workstation. Runs:

- Tailscale (already configured) for reaching `don-quixote`
- VS Code + Claude Code
- Python dev env for coding/testing
- No production services

**Testing against don-quixote:** Set in `.env`:

```
JELLYFIN_URL=http://don-quixote.tailnet.ts.net:8096
OLLAMA_HOST=http://don-quixote.tailnet.ts.net:11434
```

(Replace with actual Tailscale DNS or IP.)

This lets you run the agent from blueridge while it talks to the real
Jellyfin and Ollama on don-quixote. Media paths won't be writable from
blueridge, so use the dev-mode flag:

```bash
archive-agent daemon --dev
```

which points media paths at `./dev-media/` locally.

---

## Pre-commit hooks

Install once per clone:

```bash
pre-commit install
```

Hooks:
- `ruff check`
- `ruff format --check`
- `mypy --strict src/archive_agent`
- `pytest tests/unit/ -x --ff` (quick fail)

Commits are blocked if any fail.

---

## Systemd units (production on don-quixote)

Two units, both `--user`:

- `archive-agent-daemon.service` — the main async loop
- `archive-agent-api.service` — FastAPI HTTP service

Templates in `systemd/` directory. Install with:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable archive-agent-daemon archive-agent-api
systemctl --user start archive-agent-daemon archive-agent-api
```

Logs:
```bash
journalctl --user -u archive-agent-daemon -f
journalctl --user -u archive-agent-api -f
```

---

## Environment variables

`.env.example`:

```bash
# Required
JELLYFIN_API_KEY=
JELLYFIN_USER_ID=
TMDB_API_KEY=

# Optional (only needed if Claude provider enabled)
ANTHROPIC_API_KEY=

# Dev overrides
ARCHIVE_AGENT_CONFIG=./config.toml   # defaults to XDG location in prod
ARCHIVE_AGENT_LOG_LEVEL=DEBUG
```

---

## Verifying setup

Run in order; each should succeed before moving on:

```bash
# 1. Python env
python --version   # should be 3.11+
which python       # should be inside .venv/

# 2. Dependencies
pip list | grep -E "fastapi|pydantic|instructor|httpx"

# 3. Config parses
archive-agent config validate

# 4. State DB initializes
archive-agent state init --dry-run

# 5. Ollama reachable
archive-agent health ollama

# 6. Jellyfin reachable
archive-agent health jellyfin

# 7. Everything
archive-agent health all
```

Expected output at the end:

```json
{
  "status": "ok",
  "ollama": {"status": "ok", "model": "qwen2.5:7b", "latency_ms": 42},
  "jellyfin": {"status": "ok", "version": "10.9.8"},
  "state_db": {"status": "ok", "schema_version": 3},
  "disk": {"status": "ok", "used_gb": 0.0, "budget_gb": 500}
}
```

If anything is "degraded" or "down," fix before coding against it.
