# phase1-03: State DB schema and migrations

## Goal

SQLite schema matching `CONTRACTS.md`, with a hand-rolled migration
system and a `state.init` CLI command to bootstrap a fresh DB.

## Prerequisites

- phase1-02 (config) complete

## Inputs

- Data model from `CONTRACTS.md` section 1
- Database layout from `CONTRACTS.md` section 6

## Deliverables

1. `src/archive_agent/state/__init__.py` — public API:
   ```python
   from .models import *        # Candidate, TasteEvent, etc.
   from .db import get_db, init_db, close_db
   from .migrations import apply_pending
   ```

2. `src/archive_agent/state/models.py` — Pydantic models exactly as in
   CONTRACTS.md section 1.

3. `src/archive_agent/state/schema.sql` — DDL for all tables:
   - `candidates`
   - `taste_events`
   - `episode_watches`
   - `show_state`
   - `taste_profile_versions`
   - `downloads`
   - `librarian_actions`
   - `llm_calls`
   - `schema_version` (single-row table tracking current version)

4. `src/archive_agent/state/migrations/` directory:
   - `001_initial.py` — runs the full `schema.sql`
   - Each migration is a module with `up(conn)` and `down(conn)` functions
   - Applied in filename order

5. `src/archive_agent/state/db.py`:
   - `get_db() -> sqlite3.Connection` — returns connection, uses
     `config.paths.state_db`, enables `foreign_keys`, sets
     `journal_mode=WAL`
   - `init_db(db_path: Path) -> None` — creates parent dirs, runs pending
     migrations
   - Connection is a module-level singleton (sqlite3 is thread-safe
     enough for our use, but we'll use a lock for writes)

6. `src/archive_agent/state/queries/` — query modules per domain:
   - `candidates.py` — `upsert_candidate`, `get_by_archive_id`,
     `list_by_status`, `update_status`
   - `taste_events.py` — `insert_event`, `list_since`
   - `episode_watches.py` — `insert_watch`, `list_for_show`
   - `show_state.py` — `upsert`, `get`, `list_all_active`
   - `downloads.py` — `insert`, `update_progress`, `list_active`
   - `llm_calls.py` — `insert` (for audit)

7. CLI integration:
   - `archive-agent state init` — runs pending migrations
   - `archive-agent state info` — prints schema version, row counts per
     table
   - `archive-agent state backup <path>` — simple file copy of the DB

8. Tests in `tests/unit/state/`:
   - `test_migrations.py` — migration up/down round-trip on an in-memory
     DB
   - `test_candidates.py` — CRUD
   - `test_taste_events.py` — insert + list
   - `test_models.py` — Pydantic validation cases

## Done when

- [ ] `archive-agent state init` creates a fresh DB with all tables
- [ ] `archive-agent state info` prints schema version and 0-count rows
- [ ] Can insert/read/update each entity type via query modules
- [ ] All state tests pass
- [ ] `mypy --strict` passes on state/
- [ ] No raw `sqlite3.connect` calls outside state/db.py

## Schema details (from CONTRACTS.md, expanded)

```sql
CREATE TABLE candidates (
  archive_id TEXT PRIMARY KEY,
  content_type TEXT NOT NULL CHECK (content_type IN ('movie', 'show', 'episode')),
  title TEXT NOT NULL,
  year INTEGER,
  runtime_minutes INTEGER,
  show_id TEXT,
  season INTEGER,
  episode INTEGER,
  total_episodes_known INTEGER,
  genres TEXT NOT NULL,             -- JSON array
  description TEXT NOT NULL DEFAULT '',
  poster_url TEXT,
  formats_available TEXT NOT NULL,  -- JSON array
  size_bytes INTEGER,
  source_collection TEXT NOT NULL CHECK (source_collection IN ('moviesandfilms', 'television')),
  status TEXT NOT NULL,
  discovered_at TEXT NOT NULL,      -- ISO-8601 UTC
  jellyfin_item_id TEXT
);
CREATE INDEX idx_candidates_status ON candidates(status);
CREATE INDEX idx_candidates_content_type ON candidates(content_type);
CREATE INDEX idx_candidates_show_id ON candidates(show_id) WHERE show_id IS NOT NULL;

CREATE TABLE taste_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  content_type TEXT NOT NULL CHECK (content_type IN ('movie', 'show')),  -- NOT episode
  archive_id TEXT,
  show_id TEXT,
  kind TEXT NOT NULL,
  strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
  source TEXT NOT NULL DEFAULT 'playback',
  CHECK ((archive_id IS NOT NULL) OR (show_id IS NOT NULL))
);
CREATE INDEX idx_taste_events_timestamp ON taste_events(timestamp);

CREATE TABLE episode_watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  show_id TEXT NOT NULL,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  completion_pct REAL NOT NULL CHECK (completion_pct >= 0 AND completion_pct <= 1),
  jellyfin_item_id TEXT NOT NULL
);
CREATE INDEX idx_episode_watches_show ON episode_watches(show_id, timestamp);

CREATE TABLE show_state (
  show_id TEXT PRIMARY KEY,
  episodes_finished INTEGER NOT NULL DEFAULT 0,
  episodes_abandoned INTEGER NOT NULL DEFAULT 0,
  episodes_available INTEGER NOT NULL DEFAULT 0,
  last_playback_at TEXT,
  started_at TEXT NOT NULL,
  last_emitted_event TEXT,
  last_emitted_at TEXT
);

CREATE TABLE taste_profile_versions (
  version INTEGER PRIMARY KEY,
  updated_at TEXT NOT NULL,
  profile_json TEXT NOT NULL        -- full serialized TasteProfile
);

CREATE TABLE downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id TEXT NOT NULL,
  zone TEXT NOT NULL,               -- movies | tv | recommendations | tv-sampler
  path TEXT,
  size_bytes INTEGER,
  status TEXT NOT NULL,             -- queued | downloading | done | failed | aborted
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

CREATE TABLE librarian_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  action TEXT NOT NULL,             -- download | promote | evict | skip
  zone TEXT NOT NULL,
  archive_id TEXT,
  show_id TEXT,
  size_bytes INTEGER,
  reason TEXT NOT NULL
);

CREATE TABLE llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  provider TEXT NOT NULL,           -- ollama | claude | tfidf
  model TEXT NOT NULL,
  workflow TEXT NOT NULL,           -- rank | update_profile | parse_search
  latency_ms INTEGER NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  outcome TEXT NOT NULL             -- ok | malformed | timeout | error | fallback
);

CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
```

## Out of scope

- Actually populating any table (that's phase1-04 onwards)
- Alembic (we're using hand-rolled migrations — simpler for this scope)

## Notes

- Store lists/dicts as JSON text. Pydantic handles round-trip.
- All timestamps UTC ISO-8601 (use `datetime.now(timezone.utc).isoformat()`).
- Foreign keys kept loose: `show_id` in various tables isn't FK-constrained
  because shows aren't always in `candidates` first (TV discovery is
  messy). Enforce at application layer where needed.
- Write to DB from exactly one thread. If we need writes from multiple
  async contexts, use a queue, not threads.
