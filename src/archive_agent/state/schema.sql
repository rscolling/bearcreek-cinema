-- Initial schema for the archive-agent state DB (CONTRACTS.md §6).
-- All timestamps are ISO-8601 UTC strings (use
-- datetime.now(timezone.utc).isoformat()). List/dict fields are stored as
-- JSON text. Foreign keys are intentionally loose — show_id isn't
-- constrained because TV discovery is messy and shows don't always land
-- in `candidates` before their episodes do. Enforce at the app layer
-- when it matters.

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
    genres TEXT NOT NULL,              -- JSON array
    description TEXT NOT NULL DEFAULT '',
    poster_url TEXT,
    formats_available TEXT NOT NULL,   -- JSON array
    size_bytes INTEGER,
    source_collection TEXT NOT NULL CHECK (source_collection IN ('moviesandfilms', 'television')),
    status TEXT NOT NULL,
    discovered_at TEXT NOT NULL,       -- ISO-8601 UTC
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
    profile_json TEXT NOT NULL         -- full serialized TasteProfile
);

CREATE TABLE downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id TEXT NOT NULL,
    zone TEXT NOT NULL CHECK (zone IN ('movies', 'tv', 'recommendations', 'tv-sampler')),
    path TEXT,
    size_bytes INTEGER,
    status TEXT NOT NULL CHECK (status IN ('queued', 'downloading', 'done', 'failed', 'aborted')),
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX idx_downloads_status ON downloads(status);
CREATE INDEX idx_downloads_archive_id ON downloads(archive_id);

CREATE TABLE librarian_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('download', 'promote', 'evict', 'skip')),
    zone TEXT NOT NULL,
    archive_id TEXT,
    show_id TEXT,
    size_bytes INTEGER,
    reason TEXT NOT NULL
);
CREATE INDEX idx_librarian_actions_timestamp ON librarian_actions(timestamp);

CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('ollama', 'claude', 'tfidf')),
    model TEXT NOT NULL,
    workflow TEXT NOT NULL,            -- rank | update_profile | parse_search | health_check
    latency_ms INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'malformed', 'timeout', 'error', 'fallback'))
);
CREATE INDEX idx_llm_calls_timestamp ON llm_calls(timestamp);
CREATE INDEX idx_llm_calls_provider ON llm_calls(provider);

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
