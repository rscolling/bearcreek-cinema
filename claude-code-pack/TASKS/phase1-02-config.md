# phase1-02: Config loading

## Goal

Typed config loading from TOML with environment variable interpolation,
discoverable from standard locations or via `ARCHIVE_AGENT_CONFIG`.

## Prerequisites

- phase1-01 (scaffold) complete

## Inputs

- Config schema from `CONTRACTS.md` section 5
- Environment variable names from `ENVIRONMENT.md`

## Deliverables

1. `src/archive_agent/config.py` with:

   ```python
   class PathsConfig(BaseModel):
       state_db: Path
       media_movies: Path
       media_tv: Path
       media_recommendations: Path
       media_tv_sampler: Path

   class JellyfinConfig(BaseModel):
       url: str
       api_key: SecretStr
       user_id: str

   # ... full set per CONTRACTS.md

   class Config(BaseModel):
       paths: PathsConfig
       jellyfin: JellyfinConfig
       archive: ArchiveConfig
       tmdb: TmdbConfig
       llm: LlmConfig
       librarian: LibrarianConfig
       api: ApiConfig
       logging: LoggingConfig
   ```

2. `load_config(path: Path | None = None) -> Config`:
   - Resolution order:
     1. Explicit path argument
     2. `ARCHIVE_AGENT_CONFIG` environment variable
     3. `./config.toml` (CWD)
     4. `$XDG_CONFIG_HOME/archive-agent/config.toml`
     5. `~/.config/archive-agent/config.toml`
   - First existing file wins. If none found, raise a helpful error
     that lists all paths checked.
   - Uses `tomllib` (stdlib 3.11+).

3. Environment variable interpolation:
   - Pattern: `"${VAR_NAME}"` in string values is replaced with the env
     var value.
   - Missing env vars raise `ConfigError` naming which var was missing
     and where it was referenced.
   - Interpolation applies to strings only, not nested keys.
   - `.env` file loaded before interpolation if present (via
     `python-dotenv`).

4. `validate_config(config: Config) -> list[str]`:
   - Returns a list of warnings (non-fatal) and errors (fatal).
   - Checks: paths exist or can be created, API URLs reachable via DNS
     (not a full ping), media paths are distinct, disk budget is
     positive.
   - Used by `archive-agent config validate`.

5. CLI integration:
   - `archive-agent config show` — prints the loaded config (with
     secrets redacted as `***`)
   - `archive-agent config validate` — runs validation, prints warnings
     and errors, exits with code reflecting severity

6. Tests in `tests/unit/test_config.py`:
   - Loads from a fixture TOML file
   - Env interpolation happy path
   - Env interpolation with missing var raises with clear message
   - File not found error lists all paths
   - `SecretStr` values don't appear in `config show` output

## Done when

- [ ] `archive-agent config show` prints loaded config with secrets
  redacted
- [ ] `archive-agent config validate` runs and reports warnings/errors
- [ ] Loading fails gracefully with clear error when config file is
  missing
- [ ] All tests in `test_config.py` pass
- [ ] `mypy --strict` passes on config.py

## Verification commands

```bash
# With a good config
echo '[paths]
state_db = "/tmp/test.db"
media_movies = "/tmp/movies"
media_tv = "/tmp/tv"
media_recommendations = "/tmp/rec"
media_tv_sampler = "/tmp/sampler"

[jellyfin]
url = "http://localhost:8096"
api_key = "${JELLYFIN_API_KEY}"
user_id = "test-user-id"

# ... (full config)
' > /tmp/test-config.toml

JELLYFIN_API_KEY=abc123 ARCHIVE_AGENT_CONFIG=/tmp/test-config.toml \
  archive-agent config show

# Missing env var should fail clearly
ARCHIVE_AGENT_CONFIG=/tmp/test-config.toml archive-agent config show
# Expected: ConfigError: environment variable JELLYFIN_API_KEY not set
#   (referenced in jellyfin.api_key)

pytest tests/unit/test_config.py -v
```

## Out of scope

- Actually connecting to any external service (phase1-04+)
- Schema migrations (phase1-03)

## Notes

- Use `SecretStr` from Pydantic for all secret fields. Its `__repr__`
  redacts automatically.
- For `config show`, dump via `model_dump_json(indent=2)` — SecretStr
  serializes as `**********` by default.
- Don't use `pydantic-settings`. Overkill and brings dependencies.
  Manual TOML + env interpolation is 50 lines and stays obvious.
