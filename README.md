# Bear Creek Cinema

[![CI](https://github.com/rscolling/bearcreek-cinema/actions/workflows/ci.yml/badge.svg)](https://github.com/rscolling/bearcreek-cinema/actions/workflows/ci.yml)

**A self-hosted home cinema that learns what you like and hunts for it on
the Internet Archive.** Runs on your own server. Pulls public-domain films
and classic TV. Appears on your Roku like any other streaming app.

---

> Work in progress. Status: designing and building in public.
> See [Status](#status) for what's working today.

## The idea in one minute

You have a Jellyfin server. You have a Roku. You'd like to be watching
more classic films — public-domain noir, screwball comedy, old sitcoms —
but the Internet Archive catalog is a haystack and you don't have time
to dig through it every evening.

Bear Creek Cinema is an agent that does the digging for you. It watches
what you actually finish on Jellyfin, builds a picture of your taste,
and shops the Archive.org movie and TV collections on your behalf. Each
night it lines up a handful of recommendations. You pick one from the
couch using the companion Roku app, and it plays through your existing
Jellyfin setup.

Everything runs on your own hardware. No cloud accounts required.

## Why this exists

The major streaming services recommend what they pay to license. Public-
domain films — which is to say, much of the first half of cinema
history — are scattered across Archive.org with no serious discovery
layer. Nothing tells you that if you finished *The Third Man*, you should
probably watch *The Stranger* next, and here it is in a decent transfer.

Bear Creek Cinema is that layer.

## How it works

```
┌─────────────────┐    watches      ┌──────────────────┐
│  Internet       │ ──────────────► │  Agent on your   │
│  Archive        │                 │  home server     │
└─────────────────┘                 │  (don-quixote)   │
                                    │                  │
┌─────────────────┐    learns from  │  • Discovers     │
│  Your Jellyfin  │ ──────────────► │  • Ranks         │
│  watch history  │                 │  • Downloads     │
└─────────────────┘                 │  • Curates disk  │
                                    └────────┬─────────┘
                                             │
                                             ▼
                                    ┌──────────────────┐
                                    │  /media/         │
                                    │  (Jellyfin sees) │
                                    └────────┬─────────┘
                                             │
                                             ▼
                                    ┌──────────────────┐
                                    │  Roku: Bear Creek│
                                    │  Cinema app +    │
                                    │  Jellyfin player │
                                    └──────────────────┘
```

The agent is [Python + Ollama + SQLite](docs/ARCHITECTURE.md). The Roku
app is a thin SceneGraph browser that deep-links to the official Jellyfin
Roku client for playback. Everything is designed to run local-first with
no cloud dependency; Claude API is optional for sharper ranking when you
want it.

## Features

- **Learns your taste from what you actually finish**, not what you
  start. Episode-level playback is noise; completing a movie or bingeing
  a show is signal. See [ADR-004](claude-code-pack/DECISIONS.md#adr-004).
- **Runs your LLM locally** via [Ollama](https://ollama.com). `qwen2.5:7b`
  is the default. No data leaves your server.
- **Graceful degradation** — if Ollama is down, falls back to TF-IDF
  content similarity. Recommendations never stop coming.
- **Budget-aware disk management.** TV is expensive to store; the agent
  samples three episodes before committing to a full season.
- **Roku-native UX.** Custom app for browsing recommendations with
  natural-language voice search ("something noir and short"). Playback
  hands off to the official Jellyfin app.
- **Unified movie + TV taste profile.** A household that likes screwball
  comedy likes *His Girl Friday* and *The Dick Van Dyke Show* for the
  same reasons.

## Status

- [x] Architecture and contracts defined
- [x] Task breakdown for implementation (see
      [claude-code-pack/TASKS/](claude-code-pack/TASKS/))
- [ ] Phase 1: Plumbing (config, state DB, Jellyfin client, Ollama) —
      in progress
- [ ] Phase 2: Downloader + librarian
- [ ] Phase 3: Taste profile and ranking
- [ ] Phase 4: HTTP API
- [ ] Phase 5: Bear Creek Cinema Roku app
- [ ] Phase 6: Polish

See the [task roadmap](claude-code-pack/TASKS/README.md) for the full
build plan.

## Design highlights

A few decisions that distinguish this from a weekend scraper:

**Local-first, cloud-optional.** Ollama handles all LLM workflows by
default. A compatible `ClaudeProvider` exists for when ranking quality
matters more than privacy. Same interface, per-workflow config. See
[ADR-001](claude-code-pack/DECISIONS.md#adr-001).

**Two-stage ranking.** TF-IDF prefilters hundreds of candidates down to
~50, then the LLM reranks to a shortlist of 5-10 with natural-language
reasoning. This keeps local-model context budgets manageable. See
[ADR-002](claude-code-pack/DECISIONS.md#adr-002).

**No vector database.** At O(10⁴) candidates, `scikit-learn` in-memory
is faster and simpler than a vector store. Sometimes the right answer is
no infrastructure. See
[ADR-009](claude-code-pack/DECISIONS.md#adr-009).

**Real files, not stubs.** The recommendations library is real video
files in a dedicated Jellyfin library, moved between libraries as
lifecycle dictates. Pattern borrowed from
[jellyfin-plugin-localrecs](https://github.com/rdpharr/jellyfin-plugin-localrecs).

**Deep-link, don't rebuild.** The custom Roku app handles browsing and
selection. Playback hands off to the official Jellyfin Roku client via
ECP deep-link. Don't fork what already works.

Full rationale for every major decision is in
[DECISIONS.md](claude-code-pack/DECISIONS.md).

## For developers

This repo is built to be developed with
[Claude Code](https://claude.com/claude-code). The [`claude-code-pack/`](claude-code-pack/)
directory contains:

- `CLAUDE.md` — context for every session
- `CONTRACTS.md` — frozen interfaces (schemas, APIs, CLI)
- `GUARDRAILS.md` — hard rules that override task instructions
- `DECISIONS.md` — ADRs for decisions already made
- `TASKS/` — individual task cards with explicit "done when" criteria

If you're a human reader, start with
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the narrative version.

### Requirements

- Ubuntu 22.04+ (or similar) for the agent host
- Python 3.11+
- Jellyfin 10.9+ with at least one user's playback history
- Roku device (for the companion app, Phase 5)
- Ollama with `qwen2.5:7b` pulled (or a compatible alternative)
- ~500GB free disk (configurable; TV eats storage fast)
- Optional: Anthropic API key for the premium ranking tier

### Get started

```bash
git clone https://github.com/<you>/bearcreek-cinema.git
cd bearcreek-cinema
bash claude-code-pack/scripts/bootstrap-dev.sh
# Edit .env and config.toml
archive-agent config validate
archive-agent state init
archive-agent health all
```

More detail: [ENVIRONMENT.md](claude-code-pack/ENVIRONMENT.md).

## Project structure

```
bearcreek-cinema/
├── README.md                  # You are here
├── LICENSE
├── SESSION.md                 # Cross-session state for Claude Code
├── pyproject.toml             # Python package
├── docs/
│   ├── ARCHITECTURE.md        # Full system design
│   ├── case-study.md          # Narrative for outside readers
│   └── design-principles.md   # The stance
├── portfolio/                 # CV / Agentic Agent consulting materials
├── claude-code-pack/          # For Claude Code
│   ├── CLAUDE.md
│   ├── CONTRACTS.md
│   ├── DECISIONS.md
│   ├── GUARDRAILS.md
│   ├── TESTING.md
│   ├── ENVIRONMENT.md
│   ├── TASKS/
│   ├── fixtures/
│   └── scripts/
├── src/archive_agent/         # The Python agent
├── roku/bear-creek-cinema/    # The BrightScript Roku app
├── tests/
└── systemd/                   # Deployment unit files
```

## Related projects

This repo builds on work and ideas from:

- [jellyfin-plugin-localrecs](https://github.com/rdpharr/jellyfin-plugin-localrecs) —
  TF-IDF embeddings and virtual-library pattern
- [SuggestArr](https://github.com/giuseppe99barchetta/SuggestArr) —
  LLM-with-fallback pattern, natural-language search
- [jellyfin-roku](https://github.com/jellyfin/jellyfin-roku) — official
  Roku client we hand off to for playback
- [ia-get](https://github.com/wimpysworld/ia-get) — resumable Archive.org
  downloader
- [internetarchive](https://github.com/jjjake/internetarchive) — official
  Python library

If you're looking for a ready-to-use recommendation plugin for Jellyfin,
any of the first two are more complete than this one currently is. Bear
Creek Cinema differs in combining Archive.org as the content source, a
custom Roku browsing experience, and an agent-managed disk budget.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: issues welcome,
please read `DECISIONS.md` before proposing architectural changes.

## License

[MIT](LICENSE). Do what you want, just don't blame me.

## Acknowledgments

Built by [Rob Ross](https://github.com/<you>) of
[Agentic Agent](https://agenticagent.example), an independent AI
consulting practice focused on legacy system modernization and agentic
workflow design for mid-market enterprises.

The project's name nods to Bear Creek Trail, a corner of the Appalachian
Mountains, and to the kind of old films that still play quietly on
regional stations if you know where to find them.
