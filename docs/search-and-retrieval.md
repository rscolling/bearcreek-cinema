# Search and Retrieval Subsystem

Design for catalog search, intent search, and recommendation retrieval
across Bear Creek Cinema. Complements `docs/ARCHITECTURE.md`.

**Status:** Design. Implementation lands in Phase 3 (indexing, catalog
search) and Phase 4 (HTTP API, voice integration, routing).

---

## Three retrieval jobs, not one

Conflating these leads to a muddled system. Keep them distinct from the
start.

| Job | Example query | What the user wants |
|---|---|---|
| **Catalog search** | "the third man", "beverly hilbillies" | A known title, regardless of whether it's downloaded yet |
| **Intent search** | "something noir and short", "classic sitcoms" | Candidates matching a shape, ranked by taste |
| **Recommendation retrieval** | *no query* | Tonight's best picks |

Each is backed by different infrastructure. The query router decides
which job a user input belongs to and dispatches accordingly.

---

## Indexing strategy

Three indexes, all in SQLite. No vector database, no external search
service.

### Why SQLite is enough

The total corpus Bear Creek Cinema works with is:

- ~5,000-20,000 candidate items from Archive.org (movies + TV episodes)
- ~1,000-3,000 items actually downloaded into Jellyfin libraries
- Features per item: title, year, genres, decade, content_type, cast,
  description, TMDb IDs, runtime

That's comfortably within SQLite's happy zone for both FTS (full-text
search) and nearest-neighbor queries over precomputed TF-IDF vectors.
Adding a vector DB here would be infrastructure for problems we don't
have. (See ADR-009.)

### Index 1: FTS5 over title and description (catalog search)

```sql
CREATE VIRTUAL TABLE candidates_fts USING fts5(
    archive_id UNINDEXED,
    title,
    description,
    content='candidates',
    content_rowid='rowid',
    tokenize="trigram remove_diacritics 1"
);

-- Auto-sync with candidates table
CREATE TRIGGER candidates_fts_insert AFTER INSERT ON candidates
BEGIN
  INSERT INTO candidates_fts(rowid, archive_id, title, description)
  VALUES (new.rowid, new.archive_id, new.title, new.description);
END;
-- Corresponding UPDATE and DELETE triggers omitted for brevity.
```

Trigram tokenization is the killer feature here — it handles typos and
partial matches out of the box. "thrid man" matches "The Third Man"
because they share trigrams. Same for "beverly hilbillies" (missing the
second L) matching "The Beverly Hillbillies".

FTS5 queries return relevance-scored results (bm25). Query like:

```sql
SELECT archive_id, bm25(candidates_fts) AS score
FROM candidates_fts
WHERE candidates_fts MATCH 'third man'
ORDER BY score
LIMIT 20;
```

Fast enough (sub-millisecond) for interactive search on a laptop-class
machine, let alone `don-quixote`.

### Index 2: TF-IDF feature vectors (similarity + intent ranking)

Already planned as the ranking prefilter. Same vectors serve search.
Stored as a sparse matrix in memory, persisted to disk as a pickle for
warm restart. Features:

- Genres (one-hot)
- Decade (one-hot or binned continuous)
- Content type (one-hot: movie, show, episode)
- Director / creator tokens (from TMDb)
- Cast tokens (top 5 billed)
- Description text (tfidf over cleaned tokens)
- Runtime bucket (short / medium / long)

Cosine similarity between any two items or between a "query vector"
and the corpus is O(n) at this scale (<50ms for 20K items). No ANN
needed.

### Index 3: Metadata columns (filter queries)

Plain SQLite indexes on the `candidates` table:

```sql
CREATE INDEX idx_candidates_content_type ON candidates(content_type);
CREATE INDEX idx_candidates_year ON candidates(year);
CREATE INDEX idx_candidates_status_year ON candidates(status, year);
-- genres and cast are JSON arrays; need a derived column or a JSON function
-- in the query. Start with json_each() in WHERE; add denormalized index
-- table if it gets slow.
```

Serves "just movies from the 1940s" or "downloaded sitcoms" filter
queries without touching FTS or vectors.

---

## The query router

The piece that decides which retrieval job a user input belongs to.

```python
class QueryIntent(str, Enum):
    TITLE = "title"           # catalog search — known item lookup
    DESCRIPTIVE = "descriptive"  # intent search — shape-based
    PLAY_COMMAND = "play"     # "play the third man" — title + action
    UNKNOWN = "unknown"       # confused or out of scope

async def route_query(query: str) -> QueryRouteResult:
    """Return intent + any pre-parsed structure.

    Strategy:
    1. Cheap heuristics first (regex, keyword match)
    2. FTS probe — if query matches a title with high relevance, it's
       probably TITLE
    3. LLM fallback for ambiguous cases, with a strict Pydantic schema
       constraining output to the intents above
    """
```

Heuristics first because they're free and catch 80% of queries:

- Starts with "play" or "watch" → PLAY_COMMAND, strip the verb, treat
  remainder as title
- 1-4 words, no obvious descriptive terms → probably TITLE, try FTS
- Contains descriptive adjectives (short, long, funny, scary, noir,
  classic, etc.) or "something like X" / "more like X" → DESCRIPTIVE
- FTS probe returns a strong match (bm25 score below threshold) → TITLE
- Otherwise → LLM routing call (`llama3.2:3b`) with structured output

The LLM call is a fallback, not the default. Most voice queries won't
need it.

---

## Catalog search (Job 1)

### Behavior

User says "the third man". Agent:

1. Router identifies as TITLE
2. FTS query returns best match with bm25 score
3. If the matched candidate is already in `/media/movies` or
   `/media/tv`, return `{found: true, source: "library",
   jellyfin_item_id: "..."}`
4. If only a candidate row exists (not yet downloaded), return
   `{found: true, source: "catalog", jellyfin_item_id: null,
   downloadable: true}`
5. If no match, broaden: search Archive.org directly via the
   `internetarchive` library as a final escape hatch. If found, add to
   candidates and return `{found: true, source: "archive_live",
   adding_to_catalog: true}`
6. If still nothing, return `{found: false, suggestions: [...]}` where
   suggestions come from the top-3 FTS matches even if below threshold

### Why the live Archive.org fallback

Discovery runs hourly, so freshly-uploaded items might not be in our
candidate DB. A user searching for a specific title deserves the
courtesy of a live lookup. It's rate-limited and only triggered on
search miss, so we're not hammering Archive.org.

### Disambiguation

FTS returns scored results. If the top result has score significantly
better than the second, auto-select. If the top two are close, return
both and let the user pick.

Examples:
- "beverly hillbillies" → unambiguous, auto-select
- "night" → ambiguous (*Night of the Living Dead*, *A Hard Day's
  Night*, many episodes titled "Night"). Return top 5 with posters.

---

## Intent search (Job 2)

### Behavior

User says "something noir and short". Agent:

1. Router identifies as DESCRIPTIVE
2. Small LLM (`llama3.2:3b`) parses to `SearchFilter`:
   `{content_types: [MOVIE], genres: ["film-noir"],
     max_runtime_minutes: 100}`
3. SQLite filter query against `candidates` table returns matches
4. TF-IDF similarity ranks the filtered set against the taste profile
5. Top 10 returned with reasoning

Most of this is already in the existing architecture. The new part is
the integration with the router so that voice/text input lands in the
right place.

### NL query patterns the system must handle

Based on plausible voice/keyboard input:

| Pattern | Example | Parse to |
|---|---|---|
| Genre + runtime | "something short and noir" | `genres=[film-noir], max_runtime<=100` |
| Era | "classics from the forties" | `era=(1940, 1949)` |
| Mood | "something funny tonight" | `genres=[comedy], era<=1970 (taste-biased)` |
| Exclusion | "anything but horror" | `exclude_genres=[horror]` |
| "More like X" | "more like the third man" | Similarity query anchored on item X |
| Pure TV | "a sitcom for an hour" | `content_types=[SHOW], genres=[sitcom], episode_length_range=(20,35)` |
| Comparative | "something like the thin man but newer" | Similarity + era filter |

The "more like X" pattern is interesting — it's a hybrid of title
lookup (find X) and intent search (similar to X). Handle it as a
two-step: identify X via FTS, then run cosine similarity against X's
TF-IDF vector.

---

## Recommendation retrieval (Job 3)

Already covered by the nightly ranking pipeline. Exposed via
`GET /recommendations` and `GET /recommendations/for-tonight` endpoints.
No query, no search — the agent has opinions and returns them.

The Roku home grid is populated by this endpoint. The search screen is
populated by Jobs 1 and 2.

---

## Voice search integration (Roku)

### Roku's voice input surface

`VoiceTextEditBox` is the right node. It's a SceneGraph component that
accepts spoken input via the Roku voice remote and delivers the
transcribed string via an observer. From our app's perspective, voice
input and keyboard input are the same thing — a string.

Important constraints:

- We get transcribed text, not audio. No ASR to build.
- Transcription happens on Roku's servers; we rely on their quality.
- Minor ASR errors ("beverly hilbillies") are handled by our FTS
  trigram tokenizer.
- Voice is optional; the keyboard works too. Every feature is
  accessible without voice for users without voice remotes.

### Search scene layout

```
┌─────────────────────────────────────────────────────┐
│  [🎤] Search    "the third man"                     │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Voice or keyboard — both work               │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  Results:                                           │
│  ┌────┐  The Third Man (1949) — Ready to watch     │
│  │    │                                             │
│  └────┘                                             │
│                                                     │
│  ┌────┐  Also available:                           │
│  │    │  The Thin Man (1934) — Ready to watch     │
│  └────┘                                             │
│                                                     │
│  "More like The Third Man" →                       │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Results render progressively as the query is typed/spoken — debounced
300ms after input stops. No explicit "search" button needed.

The result state has four variants per item:

- **Ready to watch** — in Jellyfin, `content_type` = movie, direct
  deep-link available
- **Ready to watch (S01E05 next)** — in Jellyfin, TV show, with
  resume state
- **Download and watch** — in catalog but not downloaded; selecting
  kicks off download + playback
- **Not in catalog yet** — live Archive.org result; selecting
  triggers discovery + download

### Flow from voice query to playback

```
User presses 🎤 on remote: "the third man"
         │
         ▼
VoiceTextEditBox fills with "the third man"
         │
         ▼ (300ms debounce)
POST /search { "query": "the third man" } to agent API
         │
         ▼
Agent: route_query → TITLE intent
  → FTS search → best match "The Third Man (1949)"
  → check Jellyfin status → in library, ItemId = abc123
         │
         ▼
Roku app receives { items: [ { archive_id, title, year,
                               status: "ready", jellyfin_item_id } ] }
         │
         ▼
User hits OK
         │
         ▼
Roku app: POST /recommendations/{archive_id}/select
         │      (records positive signal, returns next-episode if TV)
         ▼
Roku app: ECP deep-link to Jellyfin Roku app
         │
         ▼
Jellyfin plays the film
```

Entire flow from voice → playback is ~2-3 seconds on a warm system.
Cold Ollama path (first voice query after idle) may be 6-10 seconds
while the small model loads. Mitigation: keep `llama3.2:3b` warm via
periodic keepalive pings if the daemon detects idle, so first-query
latency stays under 2 seconds.

---

## API surface (additions to existing HTTP API)

Existing `POST /search` gets extended, new endpoints added:

```
POST /search
  Body: { "query": str, "limit": int = 10, "prefer_local": bool = true }
  → 200 {
      "intent": "title" | "descriptive" | "play" | "unknown",
      "filter": SearchFilter | null,     # populated for descriptive
      "items": [SearchResultItem, ...]
    }

POST /search/similar
  Body: { "anchor_archive_id": str, "limit": int = 10 }
  → 200 { "items": [SearchResultItem, ...] }
  Behavior: cosine similarity from the anchor item's TF-IDF vector.

GET /search/autocomplete?q=prefix
  → 200 { "suggestions": [{"title": str, "archive_id": str}, ...] }
  Behavior: FTS prefix match, capped at 10. Used by the Roku search
  screen for type-ahead.
```

```python
class SearchResultItem(BaseModel):
    archive_id: str
    content_type: ContentType
    title: str
    year: int | None
    poster_url: str
    status: Literal[
        "ready",              # in Jellyfin, playable now
        "downloadable",       # in catalog, not downloaded
        "discoverable",       # live archive.org result, not yet in catalog
    ]
    jellyfin_item_id: str | None
    runtime_minutes: int | None
    next_episode: EpisodeInfo | None     # for TV shows
    relevance_score: float                # bm25 for title, cosine for similar
    match_reason: str                     # short explanation for the row
```

---

## Voice-specific edge cases

### Homophones and ASR drift

"The third man" might be transcribed as "the third men" or "the 3rd
man". Trigram FTS handles the first; a preprocessing pass normalizes
the second ("3rd" → "third", "II" → "2", etc.).

### Partial queries

Voice queries often arrive mid-thought. "Something noir" without "and
short" should still return sensible results. Design the intent parser
to treat every dimension (genre, runtime, era) as optional.

### Nonsense input

"Asdf movie" or a cat on the remote. Two-layer defense:

1. If FTS returns no matches above minimum relevance, don't trigger
   live Archive.org lookup (it's expensive and pointless)
2. Show an empty-state screen with suggestions based on current
   recommendations, not an error

### Voice-only users

Every search result card on the Roku must be voice-selectable — for
example, "Play the first one" or "Play The Third Man" should work via
Roku's native voice commands while the app is focused. This is free
on Roku as long as result cards are standard Poster nodes with
focusable trait.

---

## Indexing pipeline and refresh cadence

### When indexes update

| Event | FTS | TF-IDF | Metadata indexes |
|---|---|---|---|
| New candidate discovered | Immediate (trigger) | Nightly batch | Immediate |
| Candidate metadata enriched (TMDb) | Immediate | Nightly batch | Immediate |
| Candidate status change | Immediate | — | Immediate |
| Bulk discovery run | Batch, transactional | Rebuild after batch | Batch |

FTS lives on triggers and is always fresh. TF-IDF vectors are rebuilt
nightly because the cost of a full retrain (~30 seconds on 20K items
with scikit-learn) is trivial at this scale and simpler than
incremental updates. Metadata indexes are regular B-tree indexes,
maintained by SQLite for free.

### Warm-start persistence

On agent startup, TF-IDF model loads from `/var/lib/archive-agent/tfidf.pkl`
if present. If missing or stale (older than 24 hours), the daemon
rebuilds from scratch as its first job. Expected cold startup: ~2
minutes to be search-ready.

---

## Fallback behavior

Search must never return "search is down." Degradation tiers:

1. **All systems up:** router → LLM or FTS → full result set with
   ranking
2. **LLM down:** router skips LLM fallback, uses heuristic only;
   descriptive queries degrade to keyword search over title and
   description
3. **Ollama and Claude both down:** same as above (LLM down)
4. **TF-IDF model not loaded:** ranking uses simple genre overlap
   scoring against the taste profile's liked_genres
5. **FTS query fails:** raw LIKE fallback on title column (slow but
   correct)
6. **SQLite unavailable:** return HTTP 503 with a clear error; the
   Roku app shows an offline state

Each tier degrades legibly and visibly in logs.

---

## What's out of scope for MVP

- **Semantic/embedding search.** Not needed at this corpus size.
  Revisit if corpus grows 10x or queries get more abstract.
- **Personalization of catalog search results.** "The Third Man" should
  return *The Third Man* regardless of whose taste profile it's for.
  Intent search and recommendations are personalized; catalog search is
  not.
- **Cross-collection linking.** If the same show exists as individual-
  episode items and as a season-pack item, we treat them as separate
  catalog entries. A human can link them via the review queue in
  Phase 6.
- **Multi-word query rewriting.** Not needed; FTS5 handles this.
- **Fuzzy matching beyond trigrams.** Edit-distance libraries like
  `rapidfuzz` are tempting but overkill once trigram FTS is in place.

---

## Test plan

Unit tests:
- FTS returns expected matches for typo'd inputs on fixture data
- Query router correctly categorizes 50+ labeled example queries
- Similarity query returns items in expected relative order
- Filter parser handles each NL pattern table row correctly

Integration tests (gated):
- End-to-end search with real Ollama, real SQLite
- Live Archive.org fallback actually finds a known new title
- Voice input emulated by sending transcribed strings through POST
  /search

Golden-path test set:
- 30 labeled queries (mix of title, descriptive, and play-command)
  with expected top result
- Tracked as a regression suite; if TF-IDF retrain degrades quality,
  this set catches it
