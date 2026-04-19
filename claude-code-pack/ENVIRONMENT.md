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

Production deployment host — Ubuntu (kernel 6.8), Docker 29.2.1. Already
runs ~28 containers (Jellyfin, nginx-proxy-manager, Portainer, the ATG
agent constellation, chromadb, langfuse, ntfy, and others). The
archive-agent and Ollama each run as their own Docker stack alongside
those, following the `/home/blueridge/<stack>/docker-compose.yml`
convention used by most of the other stacks on the box.

**Login:** `ssh blueridge@192.168.1.228` (LAN) or `ssh blueridge@don-quixote`
(Tailscale MagicDNS). The Linux user is `blueridge` — earlier drafts of
this file named a `rob` user; that's incorrect.

**Hardware (verified 2026-04-18):** 31 GB RAM, 4 GB swap, 914 GB root FS
(~821 GB free), Intel HD Graphics 530 only. **No discrete GPU — Ollama
runs CPU-only.** 7B Q4 models are comfortable (~3-8 tok/s); 14B is
possible but slow.

**Host paths (bind-mount sources on the host):**

```text
/media/movies/                   # user-owned, never auto-evicted
/media/tv/                       # committed TV shows
/media/recommendations/          # agent-managed, evictable
/media/tv-sampler/               # agent-managed, evictable
/home/blueridge/archive-agent/   # the agent's stack dir + its config.toml
/home/blueridge/ollama/          # the ollama stack dir
```

**Container paths (what the code inside the agent container sees):**

```text
/media/{movies,tv,recommendations,tv-sampler}   # bind-mounted rw
/etc/archive-agent/config.toml                  # bind from stack dir
/var/lib/archive-agent/                         # state DB + logs (named volume)
```

**Jellyfin mount reality (pre-existing, do not modify):** `/media` on the
host is bind-mounted into the Jellyfin container as **read-only**.
Jellyfin only needs to read the library; the agent writes to the same
host path with rw. "Real files Jellyfin scans" works as designed — no
changes to Jellyfin's compose, which is a Portainer stack at
`/data/compose/4/docker-compose.yml`.

**Networking:** the agent's compose joins two pre-existing Docker
networks as `external: true`:

- `jellyfin_default` — reach Jellyfin at `http://jellyfin:8096`
- `ollama_default` — reach Ollama at `http://ollama:11434`

Use container aliases, never `localhost`, from inside the agent
container. The name `agent-net` is **already taken** by the ATG stack —
don't reuse it if the agent ever needs its own internal network.

**Ollama standup:** see `TASKS/phase1-07-ollama-stack.md`. Summary:
`cd /home/blueridge/ollama && docker compose up -d && docker exec ollama ollama pull qwen2.5:7b`.

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

## Docker stack (production on don-quixote)

The agent and Ollama are separate stacks, each at
`/home/blueridge/<stack>/docker-compose.yml`. The agent compose declares
**one** service that runs both the async daemon loop and the FastAPI
HTTP surface in the same process (they share state and config; splitting
them into two containers adds coordination cost for no real isolation
gain). Jellyfin is untouched — it stays a Portainer stack at
`/data/compose/4/`.

Deploy:

```bash
# Ollama first — the agent's health check depends on it
cd /home/blueridge/ollama && docker compose up -d
docker exec ollama ollama pull qwen2.5:7b
docker exec ollama ollama pull llama3.2:3b

# Agent
cd /home/blueridge/archive-agent && docker compose up -d
docker compose logs -f archive-agent
```

Restart just the agent (Ollama keeps running; models stay in RAM):

```bash
cd /home/blueridge/archive-agent && docker compose restart archive-agent
```

Logs go to Docker's json-file driver by default; tail with
`docker compose logs -f archive-agent`. If log volume becomes a problem,
switch to `local` driver with rotation in the compose file.

---

## Jellyfin library setup (one-time, per Jellyfin install)

The agent reads from Jellyfin but **never creates or modifies
libraries** (scope-limited by GUARDRAILS.md). Before the first
`archive-agent jellyfin scan` or `scan_and_resolve` call, the user
has to create two custom libraries alongside the existing Movies /
Shows ones, so the agent has somewhere for candidates to land.

In the Jellyfin Dashboard → **Libraries** → **Add Media Library**:

| Library | Content type | Folder path | Notes |
| --- | --- | --- | --- |
| Movies | Movies | `/media/movies` | usually already exists |
| Shows | TV Shows | `/media/tv` | usually already exists |
| **Recommendations** | Movies | `/media/recommendations` | **create** — custom agent zone |
| **TV Sampler** | TV Shows | `/media/tv-sampler` | **create** — custom agent zone |

Names are free-form but must have exactly the folder paths above —
`resolve_libraries` matches libraries back to zones by path, not by
name. If any of the four are missing, `archive-agent jellyfin scan`
and friends raise `MissingLibraryError` with a link back to this
section.

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

### On don-quixote (prod, inside the container)

```bash
# Stacks up and healthy
docker compose ps                       # run from /home/blueridge/archive-agent
docker network ls | grep -E "jellyfin_default|ollama_default"

# Exec into the agent container for CLI smoke tests
docker compose exec archive-agent archive-agent config validate
docker compose exec archive-agent archive-agent state init --dry-run
docker compose exec archive-agent archive-agent health ollama
docker compose exec archive-agent archive-agent health jellyfin
docker compose exec archive-agent archive-agent health all
```

### On blueridge (dev laptop, local venv)

Useful for iterative development without a rebuild cycle. Set
`JELLYFIN_URL=http://don-quixote.tailnet.ts.net:8096` and
`OLLAMA_HOST=http://don-quixote.tailnet.ts.net:11434` in `.env`, then:

```bash
python --version           # 3.11+
pip list | grep -E "fastapi|pydantic|instructor|httpx"
archive-agent config validate
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
