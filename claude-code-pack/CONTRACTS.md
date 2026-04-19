# Frozen Contracts

Interfaces in this file are decided. Changes require an ADR (see
`DECISIONS.md`) before implementation.

---

## 1. Data model (Pydantic)

These models are the single source of truth. All modules import from
`archive_agent.state.models`.

```python
from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class ContentType(str, Enum):
    MOVIE = "movie"
    SHOW = "show"
    EPISODE = "episode"


class CandidateStatus(str, Enum):
    NEW = "new"                  # just discovered
    RANKED = "ranked"            # passed TF-IDF + LLM, in shortlist
    APPROVED = "approved"        # user selected in Roku app
    SAMPLING = "sampling"        # TV: initial episodes downloading/downloaded
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"    # in /media/recommendations or /media/tv-sampler
    COMMITTED = "committed"      # in /media/movies or /media/tv (permanent)
    REJECTED = "rejected"        # user said "never again"
    EXPIRED = "expired"          # librarian evicted


class Candidate(BaseModel):
    archive_id: str                               # Archive.org identifier
    content_type: ContentType
    title: str
    year: int | None = None
    # Movie fields
    runtime_minutes: int | None = None
    # Show fields
    show_id: str | None = None                    # TMDb show id
    season: int | None = None
    episode: int | None = None
    total_episodes_known: int | None = None
    # Common
    genres: list[str] = Field(default_factory=list)
    description: str = ""
    poster_url: str | None = None
    formats_available: list[str] = Field(default_factory=list)
    size_bytes: int | None = None
    source_collection: Literal["moviesandfilms", "television"]
    status: CandidateStatus = CandidateStatus.NEW
    discovered_at: datetime
    # Jellyfin linkage (populated post-download)
    jellyfin_item_id: str | None = None


class TasteEventKind(str, Enum):
    FINISHED = "finished"                # movie finished >90%
    ABANDONED = "abandoned"              # movie stopped <50%
    REWATCHED = "rewatched"              # movie played again
    BINGE_POSITIVE = "binge_positive"    # show: 75% eps in 60d or season done
    BINGE_NEGATIVE = "binge_negative"    # show: 1-2 eps, 30d idle
    APPROVED = "approved"                # user hit Watch in Roku app
    REJECTED = "rejected"                # user hit Never
    DEFERRED = "deferred"                # user hit Not Tonight


class TasteEvent(BaseModel):
    id: int | None = None
    timestamp: datetime
    content_type: ContentType                     # MOVIE or SHOW, not EPISODE
    archive_id: str | None = None                 # for movies
    show_id: str | None = None                    # for shows
    kind: TasteEventKind
    strength: float = Field(ge=0.0, le=1.0)       # signal strength
    source: Literal["playback", "roku_api", "bootstrap"] = "playback"


class EpisodeWatch(BaseModel):
    """Raw episode playback event. Does NOT feed the taste profile directly;
    only the show state aggregator reads these."""
    id: int | None = None
    timestamp: datetime
    show_id: str
    season: int
    episode: int
    completion_pct: float = Field(ge=0.0, le=1.0)
    jellyfin_item_id: str


class ShowState(BaseModel):
    show_id: str
    episodes_finished: int
    episodes_abandoned: int
    episodes_available: int
    last_playback_at: datetime | None
    started_at: datetime
    last_emitted_event: TasteEventKind | None = None
    last_emitted_at: datetime | None = None


class EraPreference(BaseModel):
    decade: int                                   # e.g., 1940
    weight: float                                 # -1.0 to 1.0


class TasteProfile(BaseModel):
    version: int                                  # monotonic
    updated_at: datetime
    liked_genres: list[str] = Field(default_factory=list)
    disliked_genres: list[str] = Field(default_factory=list)
    era_preferences: list[EraPreference] = Field(default_factory=list)
    runtime_tolerance_minutes: int = 150
    liked_archive_ids: list[str] = Field(default_factory=list)
    liked_show_ids: list[str] = Field(default_factory=list)
    disliked_archive_ids: list[str] = Field(default_factory=list)
    disliked_show_ids: list[str] = Field(default_factory=list)
    summary: str = ""                             # LLM-maintained prose, ~300 words


class RankedCandidate(BaseModel):
    candidate: Candidate
    score: float                                  # 0.0 to 1.0
    reasoning: str                                # 1-2 sentences from LLM
    rank: int                                     # 1 = best


class SearchFilter(BaseModel):
    content_types: list[ContentType] | None = None
    genres: list[str] | None = None
    max_runtime_minutes: int | None = None
    episode_length_range: tuple[int, int] | None = None
    era: tuple[int, int] | None = None
    keywords: list[str] = Field(default_factory=list)
```

---

## 2. LLMProvider interface

```python
from typing import Protocol


class LLMProvider(Protocol):
    """Interface implemented by OllamaProvider, ClaudeProvider, TFIDFProvider."""

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
    ) -> list[RankedCandidate]:
        """Return top-n ranked candidates. Must return at least min(n,
        len(candidates)) items, ordered by rank ascending. Reasoning
        strings must be non-empty. Never raises on malformed LLM output;
        logs and falls back to a trivial ranking (by descending TF-IDF
        score) rather than raising."""
        ...

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile:
        """Produce a new profile version incorporating events. Must
        increment version. Must preserve liked/disliked IDs even if the
        LLM drops them. Prose summary must be <= 500 words."""
        ...

    async def parse_search(self, query: str) -> SearchFilter:
        """Parse a natural-language query to a structured filter. Returns
        SearchFilter with unset fields rather than raising for unparseable
        queries (e.g., SearchFilter(keywords=[query]) as last resort)."""
        ...

    async def health_check(self) -> dict[str, str]:
        """Return {"status": "ok"|"degraded"|"down", "detail": "..."}."""
        ...
```

Behavioral guarantees all implementations must honor:

- Never raise `LLMException` to callers for bad model output. Retry internally,
  fall back, or return a conservative result.
- Every call emits a row to the `llm_calls` table (model, latency, token counts
  if available, provider name).
- Timeout configuration is respected; tasks cancel cleanly.

---

## 3. HTTP API (FastAPI, LAN-only)

Base URL: `http://don-quixote.local:8787`

All responses are JSON unless otherwise noted. Errors use FastAPI's standard
problem+json. No auth in v1 (LAN-bound).

```
GET /health
  → 200 { "status": "ok", "ollama": "ok", "jellyfin": "ok",
          "disk_used_gb": 127.4, "disk_budget_gb": 500 }

GET /recommendations?type=movie|show|any&limit=10
  → 200 { "items": [RecommendationItem, ...] }

GET /recommendations/for-tonight
  → 200 { "items": [RecommendationItem x 3] }
    Behavior: filters by time of day (evening → longer runtime OK;
    late night → short content preferred).

POST /recommendations/{archive_id}/select
  Body: { "play": bool (default true) }
  → 200 { "jellyfin_item_id": "...", "play_start_ticks": 0,
          "next_episode": { season, episode } | null }

POST /recommendations/{archive_id}/reject
  → 204

POST /recommendations/{archive_id}/defer
  → 204

POST /shows/{show_id}/commit
  → 202 { "enqueued_downloads": 12, "estimated_gb": 18.4 }
    Behavior: bypass sampler, queue full-show download.

POST /search
  Body: { "query": "something noir and short", "limit": 10 }
  → 200 {
      "intent": "title" | "descriptive" | "play" | "unknown",
      "filter": SearchFilter | null,
      "items": [SearchResultItem, ...]
    }

POST /search/similar
  Body: { "anchor_archive_id": "...", "limit": 10 }
  → 200 { "items": [SearchResultItem, ...] }
  Behavior: cosine similarity from the anchor item's TF-IDF vector.

GET /search/autocomplete?q=<prefix>&limit=10
  → 200 { "suggestions": [{"title": "...", "archive_id": "..."}, ...] }
  Behavior: FTS5 prefix match. Used by the Roku search type-ahead.

GET /poster/{archive_id}
  → 200 image/jpeg (proxied from source)

GET /disk
  → 200 { "zones": [ZoneUsage, ...], "budget_gb": 500, "used_gb": 127.4 }
```

```python
class RecommendationItem(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None
    runtime_minutes: int | None
    genres: list[str]
    description: str
    poster_url: str                         # always via /poster/{id}
    reasoning: str                          # LLM rationale for the pick
    jellyfin_item_id: str | None
    # TV-specific
    season: int | None
    episode: int | None
    episodes_available: int | None
    resume_point_seconds: int | None


class ZoneUsage(BaseModel):
    zone: Literal["movies", "tv", "recommendations", "tv-sampler"]
    path: str
    used_gb: float
    file_count: int


class SearchResultItem(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None
    poster_url: str
    status: Literal[
        "ready",              # in Jellyfin library, playable now
        "downloadable",       # in candidate DB, not yet downloaded
        "discoverable",       # live archive.org result, not in catalog
    ]
    jellyfin_item_id: str | None
    runtime_minutes: int | None
    next_episode: EpisodeInfo | None     # for TV shows only
    relevance_score: float                # bm25 for title, cosine for similar
    match_reason: str                     # 1-line explanation rendered in UI


class EpisodeInfo(BaseModel):
    season: int
    episode: int
    title: str | None
    resume_point_seconds: int | None


class AutocompleteSuggestion(BaseModel):
    title: str
    archive_id: str
```

---

## 4. CLI signature

Single entry point: `archive-agent` (installed via pyproject.toml).

```
archive-agent --help
archive-agent config show
archive-agent config validate

archive-agent history dump [--type movie|show|any] [--since YYYY-MM-DD]
archive-agent history sync

archive-agent discover [--collection moviesandfilms|television|both] [--limit N]

archive-agent download <archive_id> [--dry-run]

archive-agent recommend [--type movie|show|any] [--n 5] [--provider ollama|claude|tfidf]

archive-agent profile show
archive-agent profile bootstrap [--provider ollama|claude] [--dry-run]
archive-agent profile update [--provider ollama|claude]

archive-agent librarian status
archive-agent librarian evict --dry-run
archive-agent librarian evict

archive-agent serve [--host 0.0.0.0] [--port 8787]   # runs the FastAPI service
archive-agent daemon                                  # runs the main loop
```

Exit codes:
- `0` success
- `1` user error (bad args, missing config)
- `2` infrastructure error (DB unreachable, Ollama down, etc.)
- `3` partial success (some items succeeded, some failed — check logs)

---

## 5. Config schema (TOML)

Location: `$XDG_CONFIG_HOME/archive-agent/config.toml` or `./config.toml`.

```toml
[paths]
state_db = "/var/lib/archive-agent/state.db"
media_movies = "/media/movies"
media_tv = "/media/tv"
media_recommendations = "/media/recommendations"
media_tv_sampler = "/media/tv-sampler"

[jellyfin]
url = "http://localhost:8096"
api_key = "${JELLYFIN_API_KEY}"           # env interpolation
user_id = "..."                            # the shared household account

[archive]
discovery_interval_minutes = 60
min_download_count = 100                   # quality proxy
year_from = 1920
year_to = 2000

[tmdb]
api_key = "${TMDB_API_KEY}"

[llm.workflows]
nightly_ranking = "ollama"
profile_update = "ollama"
nl_search = "ollama"

[llm.ollama]
host = "http://localhost:11434"
model = "qwen2.5:7b"
small_model = "llama3.2:3b"
timeout_seconds = 180
max_retries = 2

[llm.claude]
api_key = "${ANTHROPIC_API_KEY}"
model = "claude-sonnet-4-6"
small_model = "claude-haiku-4-5"

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

[api]
host = "0.0.0.0"
port = 8787

[logging]
level = "INFO"
format = "json"                            # json | console
```

---

## 6. Database schema (SQLite)

Managed by `archive_agent.state.migrations`. Use `alembic` or hand-written
migration scripts (decision deferred — see Task phase1-03).

Core tables (abbreviated; full DDL in `src/archive_agent/state/schema.sql`):

- `candidates` — one row per Archive.org item or episode
- `taste_events` — movie events + show-level binge events only
- `episode_watches` — raw episode playback (feeds show state aggregator)
- `show_state` — per-show aggregator state
- `taste_profile_versions` — versioned profile snapshots
- `downloads` — transfer records with zone, path, size, status
- `llm_calls` — per-call audit: provider, model, latency_ms, tokens, success

All timestamps are UTC ISO-8601 strings. SQLite datetime functions work
with these.

---

## 7. Structured logging fields

Every log line includes:

- `event` — short snake_case event name (e.g., `candidate_discovered`)
- `component` — one of: `discovery | downloader | librarian | ranker | api | jellyfin | taste`
- Relevant IDs: `archive_id`, `show_id`, `jellyfin_item_id` (only when applicable)

LLM call logs additionally include: `provider`, `model`, `latency_ms`,
`input_tokens`, `output_tokens`, `outcome` (ok|malformed|timeout|error).

Librarian operations log: `zone`, `action` (download|promote|evict|skip),
`size_bytes`, `reason`.
