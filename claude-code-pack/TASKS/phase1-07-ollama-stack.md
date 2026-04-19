# phase1-07: Ollama Docker stack on don-quixote

## Goal

Stand up Ollama as its own Docker stack at
`/home/blueridge/ollama/docker-compose.yml`, pre-pull the models the
agent depends on (`qwen2.5:7b`, `llama3.2:3b`), and publish port
`11434` so the agent container (and dev on blueridge over Tailscale)
can reach it.

## Ordering

This card is a prerequisite for `phase1-05-ollama-smoke.md` — that
card's health check hits a real Ollama endpoint. The numbering is
higher only because the card was added later; do it before phase1-05.

## Prerequisites

- SSH access to don-quixote as `blueridge`
- Docker 29.x on the host (confirmed present)
- ~10 GB free disk for the two models plus the Ollama image

## Inputs

- Deployment topology from `ENVIRONMENT.md`
- Model choices from `docs/ARCHITECTURE.md` §"Model selection matrix"

## Deliverables

1. `/home/blueridge/ollama/docker-compose.yml` with one service `ollama`:
   - Image `ollama/ollama:latest` (CPU-only — no `--gpus`, no `runtime: nvidia`)
   - Container name `ollama` (used as the Docker network alias by the
     agent; do not rename)
   - Named volume `ollama_models` → `/root/.ollama` (persists pulled
     models across container restarts — a full re-pull is ~5 GB)
   - Port mapping `11434:11434` so dev on blueridge can hit it over
     Tailscale at `http://don-quixote.tailnet.ts.net:11434`
   - `restart: unless-stopped`
   - Optional `healthcheck` hitting `/api/tags`

2. Models pulled into the volume:
   - `qwen2.5:7b` — default for ranking + profile updates
   - `llama3.2:3b` — NL search parsing

3. The default compose network `ollama_default` exists (created
   automatically by `docker compose up`). The archive-agent stack will
   reference it as `external: true`; no network changes needed here.

## Done when

- [ ] `docker compose up -d` in `/home/blueridge/ollama/` leaves the
  container healthy
- [ ] `docker exec ollama ollama list` shows both `qwen2.5:7b` and
  `llama3.2:3b`
- [ ] From the host: `curl http://localhost:11434/api/tags` returns
  JSON listing both models
- [ ] From blueridge over Tailscale:
  `curl http://don-quixote.tailnet.ts.net:11434/api/tags` returns the
  same
- [ ] A minimal prompt round-trips:
  `curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:7b","prompt":"Return the JSON {\"ok\": true}","format":"json","stream":false}'`
  returns a response with `"ok": true` parseable from the `response` field
- [ ] `docker network ls` shows `ollama_default`
- [ ] `SESSION.md` updated: current status notes Ollama is live; Recent
  Sessions entry added with outcome

## Verification commands (paste into commit message)

```bash
ssh blueridge@192.168.1.228 'cd /home/blueridge/ollama && docker compose ps'
ssh blueridge@192.168.1.228 'docker exec ollama ollama list'
curl -s http://don-quixote.tailnet.ts.net:11434/api/tags | jq '.models[].name'
```

## Out of scope

- Any Python code or `LLMProvider` wiring — that's phase1-05
- GPU acceleration — this box only has Intel HD Graphics 530
- Model quantization tuning — stick with the image's defaults (Q4)
- Auto-pull on container start — pull once manually; if that pattern
  becomes annoying, a later card can add a one-shot init container

## Notes

- First pull of `qwen2.5:7b` takes ~5 minutes on this box's connection;
  `llama3.2:3b` takes ~1 minute. Pull sequentially to avoid disk
  contention.
- CPU-only inference on this hardware is ~3-8 tok/s. First-token
  latency is the biggest UX cost; keep the 3B model warm for NL search
  (set `OLLAMA_KEEP_ALIVE=1h` in the service env if needed).
- Jellyfin's `/media` ro mount is unrelated and needs no changes.
- `agent-net` is already taken by the ATG stack — don't reuse that
  network name here.
