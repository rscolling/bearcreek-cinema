# Decisions

Decisions already made, with reasoning. Don't relitigate unless you have
new information. If you do need to revisit, add a new ADR below with the
`STATUS: SUPERSEDED` tag on the old one.

---

## ADR-001: Ollama is the default LLM; Claude is optional

**Status:** ACCEPTED

**Context:** Project goals include privacy (watch history is personal),
zero marginal cost (recommendations may run frequently), and offline
capability (home server shouldn't depend on cloud APIs).

**Decision:** Use Ollama with `qwen2.5:7b` as the default for all LLM
workflows. Claude API is available as an opt-in "premium" provider,
configured per-workflow in `config.toml`.

**Consequences:**
- Must design around ~32K effective context limits of local models
- Two-stage ranking (TF-IDF prefilter → LLM rerank) is required
- Structured output via Pydantic + Ollama's `format=schema` mode
- All LLM-facing code must tolerate occasional malformed output

---

## ADR-002: TF-IDF fallback, never fail

**Status:** ACCEPTED

**Context:** Ollama can be down, slow, or return unusable output. The
system should still produce recommendations.

**Decision:** A third `LLMProvider` implementation, `TFIDFProvider`, uses
pure `scikit-learn` cosine similarity over content feature vectors. It's
always available as a last-resort fallback. When the configured provider
fails after retries, fall through to TF-IDF, not to another LLM.

**Consequences:**
- `scikit-learn` is a required dependency
- TF-IDF model is kept warm in memory during the daemon's lifetime
- Rankings from TF-IDF lack natural-language reasoning; use a templated
  string like "Similar to X, Y that you enjoyed."

---

## ADR-003: Unified taste profile across movies and TV

**Status:** ACCEPTED

**Context:** Household watches both. A person who loves screwball comedy
loves it as film and as TV; splitting the profile loses that signal.

**Decision:** One `TasteProfile`. It contains both movie and show IDs in
the liked/disliked lists. Prose summary is content-type-agnostic. Ranking
receives mixed-type candidate pools.

**Consequences:**
- Signal weighting problem — see ADR-004
- Roku app must present mixed grids (movies and shows together) with
  type badges
- TF-IDF vector includes `content_type` as a feature so type-filtering is
  cheap

---

## ADR-004: Episodes are noise; show-binges are signal

**Status:** ACCEPTED

**Context:** TV generates 20x more playback events by volume than movies.
If every episode watch is a taste event, the profile becomes dominated by
whatever TV happened to air, not actual preferences.

**Decision:** Episode playback events flow to `episode_watches` only. They
do not generate `TasteEvent` rows. A show-state aggregator reads episode
watches and emits one `TasteEvent` per show when binge thresholds are
crossed:

- `BINGE_POSITIVE` at 75% episodes finished within 60 days, or any season
  completion
- `BINGE_NEGATIVE` at ≤2 episodes finished with 30 days inactivity
- No event otherwise

**Consequences:**
- Shows produce delayed signal (days to weeks after first watch)
- A movie watch-through and a season watch-through contribute comparable
  signal to the profile
- Need a `show_state` table to track aggregator state and prevent event
  duplication

---

## ADR-005: Librarian is a first-class subsystem, not config

**Status:** ACCEPTED

**Context:** User chose "agent decides based on disk budget." That's a
policy engine, not a flag.

**Decision:** `archive_agent.librarian` is a real module that owns all
filesystem writes under `/media/*`. It enforces zone caps, eviction
rules, sampler promotion, and download parallelism. Other modules
request placements; they do not write directly.

**Consequences:**
- All download paths go through `librarian.place()`
- Eviction runs after each download and hourly as a safety net
- Librarian has its own audit table (`librarian_actions`) for traceability

---

## ADR-006: Roku client deep-links to official Jellyfin app for playback

**Status:** ACCEPTED

**Context:** BrightScript video playback is complex and would duplicate
features already solid in the official Jellyfin Roku client.

**Decision:** "Bear Creek Cinema" is a recommendation browser. On Watch,
it sends an ECP deep-link to the Jellyfin Roku app with the item's
`contentId` (Jellyfin ItemId). Jellyfin handles playback, resume,
transcoding, subtitles, etc.

**Consequences:**
- Roku app is lean (~600 LOC BrightScript + SceneGraph)
- Requires Jellyfin Roku deep-link support (confirmed via PR #423)
- User must have the official Jellyfin Roku app installed
- Any playback issues are Jellyfin's problem, not ours

---

## ADR-007: Single Jellyfin account, retroactive tagging deferred

**Status:** ACCEPTED

**Context:** User watches on one shared Jellyfin account with a partner.
Separating preferences requires either behavior change (add a second
account) or retroactive tagging of each watch.

**Decision:** Start with one shared "household taste" profile. Build the
system to treat all playback as household signal. Add retroactive tagging
as a Phase 6 optional feature.

**Consequences:**
- Cold-start will produce recommendations that are fine for both but
  not strongly loved by either
- Not a blocker for MVP; revisit after 6 weeks of real use

---

## ADR-008: Use `internetarchive` Python library + `ia-get` for downloads

**Status:** ACCEPTED

**Context:** Archive.org downloads can be slow, flaky, and large. Need
resumable downloads, good progress reporting, and integrity verification.

**Decision:** Use the official `internetarchive` Python library for
metadata and search. For actual file downloads, shell out to `ia-get`
(Rust) as a subprocess for its resumable transfer and better behavior on
large files. Fall back to `internetarchive.download()` if `ia-get` is not
installed.

**Consequences:**
- Optional runtime dependency on `ia-get` binary
- Subprocess management for download progress
- Two code paths to test

---

## ADR-009: SQLite, not Postgres

**Status:** ACCEPTED

**Context:** Single-host deployment, moderate data volumes (O(10^4)
candidates, O(10^5) taste events over years).

**Decision:** SQLite. One file. No server to manage.

**Consequences:**
- Easy backups (copy the file)
- No network overhead
- Single-writer limitation is fine for this workload
- Revisit if multi-tenant or multi-host ever becomes a goal

---

## ADR-010: `instructor` library for unified LLM structured output

**Status:** ACCEPTED

**Context:** Want the same code path for Ollama and Claude. Both support
JSON schemas but with different idioms.

**Decision:** Use the `instructor` library to unify structured-output
calls. Pydantic models define the schema; `instructor.from_provider()`
switches backends.

**Consequences:**
- Dependency on `instructor`
- Same retry/validation logic works across providers
- Minor quirk: Ollama's JSON mode needs `mode=instructor.Mode.JSON`;
  Claude uses tool calls under the hood. Wrapper handles the difference.

---

## ADR-011: FastAPI + uvicorn, single-process

**Status:** ACCEPTED

**Context:** The HTTP API has ~10 endpoints, all low-volume. No external
exposure.

**Decision:** FastAPI with uvicorn, run as a single process under systemd.
No gunicorn, no multiple workers. Async endpoints everywhere.

**Consequences:**
- Simple ops story
- Shared in-memory caches work as expected
- Re-evaluate if we ever need to scale beyond one user's Roku

---

## ADR-012: SQLite FTS5 + in-memory TF-IDF for search, no vector DB

**Status:** ACCEPTED

**Context:** Bear Creek Cinema has three distinct retrieval jobs
(catalog search, intent search, recommendation retrieval). The total
corpus is O(10^4) candidates. Temptation exists to reach for a vector
database (pgvector, Qdrant) because "AI recommendations."

**Decision:** Three SQLite-backed indexes serve all three jobs:

1. FTS5 virtual table with trigram tokenizer — catalog search, typo
   tolerant, auto-synced via triggers
2. In-memory TF-IDF matrix (scikit-learn) — similarity and intent
   ranking, persisted nightly as a pickle for warm restart
3. Regular B-tree indexes on candidate columns — filter queries by
   decade, content_type, etc.

No external vector database.

**Consequences:**
- Zero new infrastructure dependencies for search
- Sub-millisecond FTS queries, sub-50ms similarity queries at this
  corpus size
- Trigram FTS handles typical ASR transcription drift from voice input
  without custom fuzzy logic
- If corpus grows 10x (unlikely for public-domain film curation), this
  decision needs revisiting. Until then it's the right shape.
- Also a portable lesson for consulting work: match the index to the
  data scale, not the buzzword.

---

## ADR-013: Netflix-style 3-thumb explicit show ratings

**Status:** ACCEPTED

**Context:** The taste profile (ADR-003, ADR-004) currently learns
only from implicit playback signal — finishing a movie, binge-
completing a show, abandoning a show. That works for households who
watch many things, but it's slow (needs multiple events to converge),
ambiguous (abandoning after one episode could be "not for me" or "I'll
get back to it"), and opaque (user can't directly tell the agent
"don't recommend more like this"). Netflix's 3-thumb system solves
that with a minimal explicit-signal UI: thumbs-down, thumbs-up, double
thumbs-up.

**Decision:** Add Netflix-style 3-thumb ratings as an **augment** to
implicit signal (not a replacement). Ratings are:

- **per-show**, not per-episode (matches Netflix; episodes roll up to
  show in the taste profile anyway — ADR-004)
- **latest-wins**: ``taste_events`` stays append-only so we keep the
  audit history, but the ranker computes each show's current rating as
  the most-recent ``roku_api``-sourced rating event — not a sum of
  thumbs over time
- **Roku-only for now**: no HTTP ``/rate`` endpoint in phase 4. The
  Roku app records ratings by calling into the same in-process code
  path as phase5's /select (via a new ``/shows/{id}/rate`` endpoint
  added when phase5 lands)

Three new ``TasteEventKind`` variants, mapped to the existing
``strength: float (0..1)`` field:

| thumb | `TasteEventKind` | strength |
| --- | --- | --- |
| 👎 | `RATED_DOWN` | 0.9 |
| 👍 | `RATED_UP` | 0.6 |
| 👍👍 | `RATED_LOVE` | 1.0 |

Strength's polarity follows the kind (same pattern as REJECTED /
ABANDONED / BINGE_NEGATIVE — high strength with a "negative" kind
means strong dislike). All three have ``content_type=SHOW`` and
``source="roku_api"``; the existing ``taste_events`` schema already
enforces the show/movie content_type check and the archive_id-or-
show_id invariant.

**Consequences:**

- One explicit thumb quickly delivers what half a dozen implicit
  events would approximate. A brand-new user can seed the profile in
  minutes.
- Ranker math (phase3-03 onward) treats ratings as strong priors:
  RATED_LOVE pushes "more like this" heavily; RATED_DOWN excludes
  similar shows from recommendations for a while (not permanently —
  tastes change).
- Append-only history means a user who flips 👎 → 👍 → 👎 again leaves
  three rows; reader picks the newest by ``max(timestamp)`` per
  ``show_id`` where ``source='roku_api'`` AND ``kind`` in the three
  RATED variants.
- No schema change needed: ``taste_events.kind`` has no ``CHECK``
  constraint and already allows ``content_type='show'``. ``source``
  defaults to ``'playback'`` so a Roku writer must pass
  ``source='roku_api'`` explicitly, which also cleanly distinguishes
  explicit ratings from implicit ones.
- Strength numbers (0.9 / 0.6 / 1.0) are a starting point, tuneable
  as phase3 ranking gets calibrated against real data. Don't treat
  them as fixed.
- Per-episode ratings are **deferred**, not rejected — if Phase 6
  feedback suggests users want them, revisit.

---

## ADR template (use for new decisions)

```
## ADR-NNN: Title

**Status:** PROPOSED | ACCEPTED | SUPERSEDED

**Context:** What problem is this solving?

**Decision:** What are we doing?

**Consequences:** What changes because of this? What do we have to be
careful about?
```
