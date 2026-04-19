# Archive.org → Jellyfin Recommendation Agent

A self-hosted agent that curates public-domain **movies and TV** from
Archive.org (`/details/moviesandfilms` and `/details/television`), learns the
household's taste from Jellyfin playback, and surfaces recommendations through
a custom Roku app that deep-links into the Jellyfin Roku player.

**LLM stack:** Local-first via Ollama on `don-quixote`. Claude API is an
optional "premium" tier for the ranking step. Everything can run entirely
with local models.

---

## Goals

- Discover candidates from both the movies and television collections
- Learn **one** unified household taste across films and TV
- Present a small, high-quality shortlist for approval via the Roku app
- Auto-download approvals into Jellyfin with proper structure for each type
- Run fully locally — no cloud dependencies required
- Support natural-language search ("something noir and short", "a short
  sitcom for tonight") from the remote

## Non-goals

- A full-featured Jellyfin Roku client (deep-link into the official one)
- A polished multi-user system — two viewers, one Jellyfin account
- Training a real ML model — Ollama does the ranking
- Cloud dependency — Claude is strictly optional

---

## Why local LLM (Ollama), honestly

**What we gain:** zero per-token cost; watch history and taste profile never
leave `don-quixote`; no rate limits; no network round-trips; the system
keeps working if internet is down.

**What we lose:** a 7B-14B local model produces noticeably less nuanced
rankings than Claude Sonnet. "Likes Welles but not the talky procedurals"
is where smaller models blur. Long-context synthesis is where they lose
the thread. First-token latency is worse on CPU — fine for batch jobs,
awful for interactive use.

**The resolution:** Ollama as default. Claude as optional, per-workflow,
when ranking quality matters more than cost and privacy. One
`LLMProvider` interface, two implementations.

Also the right pattern to show clients: local-first, cloud-optional, with
graceful fallback is how most real AI systems ship to privacy-conscious
mid-market enterprises.

---

## Why movies and TV in parallel (and the pitfall)

TV differs from film in almost every subsystem:

- **Discovery:** a "show" on Archive.org can be one item with many files,
  or many items (one per episode), or both duplicated.
- **Metadata:** TMDb has separate movie/TV endpoints, hierarchical
  (show → season → episode).
- **Taste signal:** finishing a film is one clean event; "finishing" a TV
  show takes weeks and lots of small events. Abandoning after one episode
  is usually negative; abandoning after sixteen is usually positive.
- **Downloads:** a film is ~1-4 GB; a show run can be 50 GB+. Disk budget
  becomes a real subsystem.
- **Jellyfin:** different library type, different folder layout
  (`Show/Season 01/S01E01.mp4`), different metadata files.
- **Roku app:** needs series/season/episode browsing, not a flat grid.

**The pitfall if done naively:** building both tracks in parallel doubles
scope and stalls shipping.

**Mitigation — parallel design, sequential delivery within each phase.**
Abstractions that support both land in Phase 1 (data model, downloader
interfaces, Jellyfin structure). But each phase gets exercised on movies
first and TV second, within the same phase. You don't build two pipelines;
you build one pipeline that handles both types, and you turn on the TV path
a little later than the movie path inside each weekend.

---

## Unified taste profile — the signal-weighting problem

Naively concatenating movie events and TV events breaks the profile: TV
generates 20× more playback events by volume, so "watched lots of TV"
drowns out film preferences.

**Rule: episodes are noise; show-binges are signal.** Concretely:

- A movie playback event (finished, abandoned, rewatched) flows directly
  into the profile.
- An episode playback event updates *show-level resume state* only. It
  does **not** touch the taste profile.
- A show emits a taste event when it crosses a threshold:
  - *Positive* when 75%+ of available episodes are completed within 60 days
  - *Positive* when a season is finished in any timeframe
  - *Negative* when only 1-2 episodes are watched with no activity for 30 days
  - *Neutral* otherwise

This means a "watched 3 episodes and stopped" show sits in limbo for a
month before being scored, which is correct — sometimes that's "dropped
it," sometimes that's "I'll get back to it."

**Result:** a movie-binge and a TV-binge contribute comparable signal
to the profile. A single episode watched casually contributes nothing
to taste, only to resume state.

The profile prose itself doesn't care about the distinction:
> Household likes screwball comedy across eras — shows up in film picks
> (*His Girl Friday*, *The Thin Man*) and TV (*The Dick Van Dyke Show*).
> Avoids slow pacing regardless of format.

---

## The librarian — disk budget as a first-class subsystem

User choice: agent manages disk based on a budget. That's a real policy
engine. Call it the *librarian*.

**Zones:**

| Zone                         | Purpose                          | Managed by    | Eviction           |
|------------------------------|----------------------------------|---------------|---------------------|
| `/media/movies`              | Permanent film library           | Manual/agent  | Never auto-evicted |
| `/media/tv`                  | Committed TV shows               | Agent         | Slow, by rule       |
| `/media/recommendations`     | Candidate movies awaiting review | Agent         | 14 days untouched   |
| `/media/tv-sampler`          | 1-3 episodes per candidate show  | Agent         | 30 days untouched   |

**Hard cap:** `max_disk_gb` in config. Total across all agent-managed
zones must stay under this. `/media/movies` is outside the budget (user
owned).

**TV download policy (sampler-first):**

1. New TV recommendation approved → download 3 episodes into
   `/media/tv-sampler/{Show}/Season 01/`
2. If 2+ episodes finished within 14 days → promote to committed,
   download remainder of Season 1 into `/media/tv/`
3. If Season 1 finishes → download remaining seasons, one at a time
4. If sampler ignored for 30 days → delete from sampler

This matches how a household actually discovers TV: try a couple
episodes, decide, commit or bail.

**Eviction rules (when over budget):**

1. Recommendations untouched 14+ days → delete oldest first
2. Sampler shows untouched 30+ days → delete
3. Never evict anything in `/media/movies` or committed `/media/tv`
4. Notify user if still over budget after steps 1-3 (don't silently
   delete committed content)

**Bounded parallelism:**

- At most N simultaneous downloads (default 2)
- At most X GB actively transferring at once (default 20)
- Respect Archive.org throttling; retry with backoff

---

## Hardware reality check (don-quixote)

Verify before committing:

| Resource           | Minimum for reasonable perf  | Notes                             |
|--------------------|------------------------------|-----------------------------------|
| RAM                | 16 GB (7B models at Q4)      | 32 GB unlocks 14B models          |
| GPU                | Optional, changes everything | 8 GB VRAM → 7B; 16 GB → 14B       |
| Disk               | ~10 GB per Ollama model      | Plus the max_disk_gb budget       |
| CPU inference OK?  | Yes, slowly                  | ~3-8 tokens/sec on modern CPU     |

Run before coding:

```bash
free -h               # RAM check
lspci | grep -i vga   # GPU check
df -h /               # disk check
```

---

## Model selection matrix

| Role                                | Default (Ollama)         | Fallback / Alt              | Premium (Claude)        |
|-------------------------------------|---------------------------|-----------------------------|--------------------------|
| Nightly ranking (JSON shortlist)    | `qwen2.5:7b`             | `llama3.2:8b`               | `claude-sonnet-4-6`     |
| Taste profile updates (prose)       | `qwen2.5:7b`             | `llama3.1:8b`               | `claude-sonnet-4-6`     |
| NL search parsing                   | `llama3.2:3b`            | `phi3:mini`                 | `claude-haiku-4-5`      |
| Emergency fallback (no LLM at all)  | TF-IDF cosine similarity | —                           | —                        |

**Why `qwen2.5:7b`.** Strong at structured JSON, good system prompt
following, solid function-calling at 7B size.

**Why a 3B model for NL search.** "Something noir and short" → structured
filter is a tiny task. A 3B model runs fast on CPU, keeps the Roku search
box feeling responsive.

**Why a TF-IDF floor.** From the `localrecs` Jellyfin plugin pattern. If
Ollama is down, the system still produces recommendations via cosine
similarity over genre/director/decade/content-type vectors. Never an
empty list.

---

## The single-account problem

The household watches on one Jellyfin account, so raw playback history is
mixed signal. Plan: start as one shared "household taste" profile. Add
retroactive tagging in Phase 6 if it feels too blunt.

---

## System overview

```
┌──────────────────────┐  ┌──────────────────────┐   ┌──────────────────────┐
│   Archive.org        │  │   Archive.org        │   │   Roku devices       │
│   moviesandfilms     │  │   television         │   │  ┌────────────────┐  │
└──────────┬───────────┘  └──────────┬───────────┘   │  │ Bear Creek     │  │
           │                         │                │  │ Cinema         │  │
           │                         │                │  └───────┬────────┘  │
           └────────┬────────────────┘                │          │           │
                    │                                 │  deep-link│          │
                    ▼                                 │          ▼           │
┌───────────────────────────────────────────────────────────────────────────┐
│                    Agent (don-quixote)                                     │
│                                                                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────┐        │
│  │ Discovery  │→ │ Candidates │→ │ TF-IDF     │→ │ Ranker        │        │
│  │ (movies &  │  │ DB         │  │ prefilter  │  │ - Ollama (def)│        │
│  │  TV)       │  │            │  │            │  │ - Claude (opt)│        │
│  └────────────┘  └────────────┘  └────────────┘  │ - TF-IDF (bk) │        │
│                        ▲                          └───────┬───────┘        │
│                        │                                  │                │
│                   ┌────┴──────┐                           ▼                │
│                   │  Taste    │           ┌──────────────────────┐        │
│                   │  profile  │◄──────────│  HTTP API (FastAPI)  │        │
│                   │  (unified │           │  /recommendations    │        │
│                   │   shared) │           │  /search             │        │
│                   └────▲──────┘           │  /select             │        │
│                        │                  └──────────┬───────────┘        │
│                        │                             │                    │
│                   ┌────┴──────┐                      │                    │
│                   │ Jellyfin  │                      │                    │
│                   │ client    │                      │                    │
│                   └───────────┘                      │                    │
│                                                      ▼                    │
│                                            ┌─────────────────┐            │
│                                            │  Librarian      │            │
│                                            │  (disk budget,  │            │
│                                            │  tiered zones,  │            │
│                                            │  sampler-first) │            │
│                                            └────────┬────────┘            │
│                                                     │                     │
│                                                     ▼                     │
│                                           ia-get / internetarchive        │
│                                                     │                     │
│                                                     ▼                     │
│       ┌───────────────┬─────────────┬───────────────┬─────────────────┐   │
│       │/media/movies  │/media/tv    │/media/recomm. │/media/tv-sampler│   │
│       └───────────────┴─────────────┴───────────────┴─────────────────┘   │
│                        ▲                                                  │
│                        │ scans                                            │
│                 ┌──────┴──────┐                                           │
│                 │  Jellyfin   │                                           │
│                 └─────────────┘                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Data model (designed for both types from day one)

```python
class ContentType(str, Enum):
    MOVIE = "movie"
    SHOW = "show"
    EPISODE = "episode"

class Candidate(BaseModel):
    archive_id: str              # Archive.org identifier
    content_type: ContentType
    title: str
    year: int | None
    # Movie fields (None for shows)
    runtime_minutes: int | None
    # Show fields (None for movies)
    show_id: str | None          # TMDb show id, for grouping episodes
    season: int | None
    episode: int | None
    total_episodes_known: int | None
    # Common
    genres: list[str]
    description: str
    poster_url: str | None
    formats_available: list[str]
    size_bytes: int | None
    source_collection: str       # "moviesandfilms" or "television"
    status: Literal["new", "ranked", "approved", "sampling",
                    "downloading", "downloaded", "committed",
                    "rejected", "expired"]

class TasteEvent(BaseModel):
    timestamp: datetime
    content_type: ContentType    # "movie" or "show" — never "episode"
    archive_id: str | None       # for movies
    show_id: str | None          # for shows
    kind: Literal["finished", "abandoned", "rewatched",
                  "binge_positive", "binge_negative",
                  "approved", "rejected"]
    strength: float              # 0.0 to 1.0

class TasteProfile(BaseModel):
    updated_at: datetime
    version: int
    # Structured
    liked_genres: list[str]
    disliked_genres: list[str]
    era_preferences: dict[str, float]
    runtime_tolerance_minutes: int
    liked_archive_ids: list[str]
    liked_show_ids: list[str]
    disliked_archive_ids: list[str]
    disliked_show_ids: list[str]
    # Prose
    summary: str                 # ~300 words, LLM-maintained
```

`content_type` being first-class everywhere means filters like "just
movies tonight" or "30-minute sitcoms" are trivial, and the TF-IDF
prefilter can treat it as a feature.

---

## Components

### 1. LLM provider abstraction (unchanged)

```python
class LLMProvider(Protocol):
    async def rank(self, profile: TasteProfile,
                   candidates: list[Candidate], n: int = 5) -> list[Ranked]: ...
    async def update_profile(self, current: TasteProfile,
                             events: list[TasteEvent]) -> TasteProfile: ...
    async def parse_search(self, query: str) -> SearchFilter: ...
```

Implementations: `OllamaProvider` (default), `ClaudeProvider` (optional),
`TFIDFProvider` (fallback). Using `instructor` library to unify Pydantic
schema validation across providers.

### 2. Discovery worker

Runs hourly, queries **both** collections:

- `moviesandfilms` → movies, with filters for download count, year, formats
- `television` → shows and episodes, with the same filters plus heuristics
  to group episodes into shows

**TV grouping heuristic.** Archive.org's television items are messy. The
agent tries, in order:
1. TMDb search on the item title — if it matches a known show with a
   season/episode pattern in the item name (e.g. "S01E03", "Episode 5"),
   associate with that show
2. Look for bulk-show items where files inside the item are episodes
3. Flag ambiguous items for the low-confidence review queue

Grouping is best-effort. An ungroupable TV item gets stored as a
standalone episode with `show_id = None` and can still be ranked and
shown.

### 3. Taste profile (unified, signal-weighted)

Nightly update reads:
- All new movie playback events directly
- Show-level binge events (positive/negative/neutral) computed by the
  show-state aggregator
- Approvals and rejections from the HTTP API

Produces new structured + prose profile. Prose generation is a separate
small prompt to keep token budget manageable on local models.

### 4. Show state aggregator

Runs nightly. For each show with episodes in the library:
- Tracks episodes finished, episodes abandoned, last-playback timestamp
- Emits `binge_positive` when threshold hit (75% of known episodes in 60 days,
  or full season finish)
- Emits `binge_negative` after 30 days of inactivity with ≤2 episodes watched
- Emits nothing otherwise (in-progress)

State persists so events aren't re-emitted; profile updates are idempotent.

### 5. Two-stage ranking

**Stage 1 — TF-IDF prefilter:** From N candidates, pick top K by cosine
similarity to taste vector. Features include `content_type`, so
"just movies" filters are essentially free.

**Stage 2 — LLM rerank:** LLM receives top K candidates + prose profile,
returns top 5-10 with reasoning. Critical for Ollama because small
models don't handle large candidate pools.

### 6. Natural-language search

NL query → small Ollama model → structured `SearchFilter`:

```python
class SearchFilter(BaseModel):
    content_types: list[ContentType] | None      # None = any
    genres: list[str] | None
    max_runtime_minutes: int | None              # movies
    episode_length_range: tuple[int, int] | None # TV
    era: tuple[int, int] | None
    keywords: list[str]
```

"Something noir and short" →
`{content_types: [MOVIE], genres: ["film-noir"], max_runtime_minutes: 100}`

"A short sitcom for tonight" →
`{content_types: [SHOW], genres: ["sitcom"], episode_length_range: [20, 35]}`

### 7. Content placement in Jellyfin

Following the `jellyfin-plugin-localrecs` "real files in virtual libraries"
pattern — no stub MP4 hackery.

**Movies:**
- Recommendations → `/media/recommendations/{Title} ({Year})/{Title} ({Year}).mp4`
- Approved + watched → move to `/media/movies/{Title} ({Year})/...`

**Shows:**
- Sampler episodes → `/media/tv-sampler/{Show}/Season 01/{Show} - S01E01 - {Title}.mp4`
- Promoted → move entire show to `/media/tv/{Show}/Season XX/...`
- Jellyfin library type is "TV Shows" for /tv and /tv-sampler; "Movies" for the
  other two

Files are real. Jellyfin scans, recognizes, displays. Roku sees them on the
official Jellyfin app as well as on Bear Creek Cinema.

### 8. HTTP API for the Roku app

FastAPI on don-quixote LAN:

```
GET  /recommendations                    → shortlist across both types
GET  /recommendations?type=movie         → filtered
GET  /recommendations/for-tonight        → 3 picks based on time of day
POST /recommendations/{id}/select        → positive signal + prepare playback
POST /recommendations/{id}/reject        → negative signal
POST /recommendations/{id}/defer         → mild negative
POST /shows/{show_id}/commit             → force full download of a show
POST /search                             → NL search
GET  /poster/{id}                        → proxied poster
GET  /health                             → Ollama, Jellyfin, disk status
GET  /disk                               → budget usage by zone
```

Select behavior differs by type:
- Movie → returns `{jellyfin_item_id, play_start: 0}`
- Show → returns `{jellyfin_item_id: <next unwatched episode>, play_start: <resume>}`

The Roku app doesn't need to know about episodes or seasons unless user
explicitly browses them.

### 9. Bear Creek Cinema — the Roku app

Scenes:

- **Home grid** — unified poster wall with small corner badge for type
  (movie reel icon, TV icon)
- **Detail (movie)** — poster, plot, runtime, reasoning;
  buttons: Watch / Not Tonight / Never / More Like This
- **Detail (show)** — poster, plot, number of available episodes,
  reasoning, resume state;
  buttons: Watch (next ep) / Browse Episodes / Commit Full Show /
  Not Tonight / Never / More Like This
- **Episode browser** — season/episode grid for committed shows
- **Search** — voice NL query + results, filterable by type
- **Settings** — agent URL, provider selection, "show me movies only / TV
  only / both"

"Watch" on a movie: deep-links to Jellyfin with the movie's ItemId.
"Watch" on a show: deep-links to Jellyfin with the next-unwatched episode's
ItemId. Resume point is handled by Jellyfin as normal.

### 10. Librarian (disk budget enforcement)

Runs after every download completes and hourly as a safety net. Enforces
zone caps, eviction rules, and sampler promotion.

Config:
```toml
[librarian]
max_disk_gb = 500
recommendations_ttl_days = 14
tv_sampler_ttl_days = 30
max_concurrent_downloads = 2
max_bytes_in_flight_gb = 20

[librarian.tv]
sampler_episode_count = 3
promote_after_n_finished = 2
promote_window_days = 14
```

### 11. State

SQLite at `/var/lib/archive-agent/state.db`. Tables:

- `candidates` — discovery results with status
- `downloads` — transfers with size, path, zone
- `taste_events` — only movie events + show binge events (not episodes)
- `episode_watches` — raw episode playback events (fodder for aggregator)
- `show_state` — per-show aggregator state (progress, emitted events)
- `taste_profile` — versioned snapshots
- `llm_calls` — every LLM interaction with model, tokens, latency
- `disk_snapshots` — periodic capture for the /disk dashboard

---

## Stack

| Concern                   | Choice                                |
|---------------------------|---------------------------------------|
| Language                  | Python 3.11+                          |
| LLM (default)             | Ollama, `qwen2.5:7b` Q4               |
| LLM (optional)            | Anthropic Claude via `anthropic` SDK  |
| LLM interface             | `instructor` library                  |
| Structured output         | Pydantic                              |
| Archive.org               | `ia-get` + `internetarchive` library  |
| Jellyfin                  | Direct REST via `httpx`               |
| Metadata                  | TMDb (movie + TV endpoints)           |
| Vectors / prefilter       | `scikit-learn` TF-IDF + cosine        |
| HTTP API                  | FastAPI + uvicorn                     |
| State                     | SQLite via stdlib                     |
| Job loop                  | `asyncio` with in-proc queue          |
| Process management        | systemd user services × 2             |
| Config                    | TOML                                  |
| Logging                   | `structlog` → journald                |
| Roku app                  | BrightScript + SceneGraph XML         |

---

## Phased build plan

Design covers both movies and TV from day one; delivery exercises movies
first, TV second within each phase.

### Phase 1 — plumbing (one weekend)

- [ ] Project scaffold, config, SQLite schema with full type-aware model
- [ ] Jellyfin client: auth, fetch history for both movies and episodes
- [ ] Ollama install verified, `qwen2.5:7b` pulled
- [ ] `instructor` smoke test: Pydantic JSON round-trip
- [ ] **Done when:** `archive-agent history dump --type movie` and
  `--type show` both print sensible summaries

### Phase 2 — downloader + librarian (one weekend)

- [ ] Archive.org discovery for both collections
- [ ] `ia-get` wrapper with multi-file item support
- [ ] Format selection (MP4 preferred)
- [ ] Librarian: zone management, budget enforcement
- [ ] File placement into Jellyfin (movies path)
- [ ] **Done when:** `archive-agent download <movie-id>` works end-to-end;
  librarian correctly evicts on budget pressure. TV path scaffolded but
  only tested on one show.

### Phase 3 — taste + ranking (one weekend)

- [ ] TMDb enrichment for both types
- [ ] Show state aggregator + binge event emission
- [ ] TF-IDF prefilter with `content_type` feature
- [ ] `OllamaProvider` + `TFIDFProvider`
- [ ] Bootstrap script from existing movie history
- [ ] **Done when:** `archive-agent recommend` returns sensible mixed
  shortlist of movies and shows with reasoning

### Phase 4 — HTTP API (one weekend)

- [ ] FastAPI service with all endpoints
- [ ] NL search via small Ollama model, content-type-aware
- [ ] Recommendations + TV sampler libraries wired in Jellyfin
- [ ] Select-triggers-download flow for both types
- [ ] **Done when:** `curl` a movie `/select` and a show `/select`; both
  produce playable content in Jellyfin

### Phase 5 — Bear Creek Cinema (one to two weekends)

- [ ] SceneGraph project scaffold
- [ ] Home grid with type badges
- [ ] Movie and show detail scenes
- [ ] Episode browser scene
- [ ] Voice search
- [ ] Deep-link to Jellyfin on Watch
- [ ] Sideload to Roku in dev mode
- [ ] **Done when:** full loop from couch with both content types

### Phase 6 — polish

- [ ] `ClaudeProvider` as premium tier
- [ ] Per-viewer tagging prompt
- [ ] Web dashboard on don-quixote (budget, queue, LLM calls)
- [ ] Notifications (ntfy.sh)
- [ ] Review queue for low-confidence TMDb matches

---

## Risks and open questions

**Archive.org TV metadata quality.** Far messier than movies. Many items
lack clear season/episode markers. Mitigation: show the ungroupable items
as standalone episodes anyway; they still have value for the taste signal
and can be individually played.

**Ollama quality on mixed movie+TV ranking.** A 7B model must reason across
both types simultaneously. If quality suffers, options: run separate movie
and TV ranking passes and interleave results, or use Claude for the
bootstrap profile generation only.

**Disk budget in practice.** 500 GB fills up fast with TV. The sampler-first
strategy is critical to not blowing the budget on shows nobody ends up
watching. Watch budget utilization in Phase 5 and tune.

**Cold first-token latency on CPU.** ~10 sec for NL search. Mitigations:
keep the small model warm, or accept the delay.

**Roku app deep-link with show episodes.** Verify early (Phase 5) that the
Jellyfin Roku app accepts an episode ItemId via deep-link and resumes
correctly. PR #423 suggests yes, but episode-level deep-linking has less
testing than movie-level.

**The single-account limitation** still smooths two people's tastes into
one. Phase 6 tagging is the escape hatch.

---

## What this isn't doing (and why)

- **No fine-tuned model.** Base models are fine; a LoRA over our history
  would overfit badly.
- **No real-time streaming from Archive.org.** Downloads first.
- **No vector DB.** TF-IDF prefilter in-memory is enough at this scale.
- **No public-facing API.** LAN only unless explicitly exposed via Tailscale.
- **No custom Jellyfin client.** Deep-link handoff is dramatically simpler
  than forking jellyfin-roku.
- **No episode-level taste events.** Would swamp the profile with noise.
