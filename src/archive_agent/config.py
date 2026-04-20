"""Typed TOML configuration with env-var interpolation.

Contract: see `claude-code-pack/CONTRACTS.md` §5.

Loader resolution order (first existing file wins):
  1. explicit path argument to ``load_config``
  2. ``ARCHIVE_AGENT_CONFIG`` environment variable
  3. ``./config.toml`` (CWD)
  4. ``$XDG_CONFIG_HOME/archive-agent/config.toml``
  5. ``~/.config/archive-agent/config.toml``

Env interpolation: ``${VAR}`` in any TOML string is substituted with the
process env var; ``${VAR:-fallback}`` uses the fallback when the var is
unset (matches bash). Missing vars without a fallback raise
``ConfigError`` naming both the variable and the TOML path that
referenced it. A ``.env`` file in CWD is loaded before interpolation.
"""

from __future__ import annotations

import os
import re
import socket
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, PositiveInt, SecretStr, ValidationError, field_validator


class ConfigError(Exception):
    """Raised for any fatal problem during config load or validation."""


# --- Pydantic models -----------------------------------------------------------


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


class ArchiveConfig(BaseModel):
    discovery_interval_minutes: PositiveInt = 60
    min_download_count: int = Field(default=100, ge=0)
    year_from: int = 1920
    year_to: int = 2000


class TmdbConfig(BaseModel):
    api_key: SecretStr


class LlmWorkflowsConfig(BaseModel):
    nightly_ranking: Literal["ollama", "claude", "tfidf"] = "ollama"
    profile_update: Literal["ollama", "claude", "tfidf"] = "ollama"
    nl_search: Literal["ollama", "claude", "tfidf"] = "ollama"


class LlmOllamaConfig(BaseModel):
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    small_model: str = "llama3.2:3b"
    timeout_seconds: PositiveInt = 180
    max_retries: int = Field(default=2, ge=0)
    # Context window we ask Ollama to allocate per request. Must be large
    # enough that a 50-candidate rank prompt fits with headroom (see
    # archive_agent.testing.token_budget.check_prompt_fits).
    num_ctx: PositiveInt = 8192


class LlmClaudeConfig(BaseModel):
    api_key: SecretStr | None = None
    model: str = "claude-sonnet-4-6"
    small_model: str = "claude-haiku-4-5"
    # Enough for 10 picks + full profile summary; bigger wastes money.
    max_tokens: PositiveInt = 4096

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v: Any) -> Any:
        # A missing-but-fallback env interpolation yields "" — coerce to
        # None so downstream code can use `if config.llm.claude.api_key`
        # without having to unwrap SecretStr("").
        if v == "":
            return None
        return v


class LlmConfig(BaseModel):
    workflows: LlmWorkflowsConfig = Field(default_factory=LlmWorkflowsConfig)
    ollama: LlmOllamaConfig = Field(default_factory=LlmOllamaConfig)
    claude: LlmClaudeConfig = Field(default_factory=LlmClaudeConfig)


class TasteConfig(BaseModel):
    binge_positive_completion_pct: float = Field(default=0.75, ge=0.0, le=1.0)
    binge_positive_window_days: PositiveInt = 60
    binge_positive_strength: float = Field(default=0.8, ge=0.0, le=1.0)
    binge_negative_max_episodes: PositiveInt = 2
    binge_negative_inactivity_days: PositiveInt = 30
    binge_negative_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    season_complete_min_episodes: PositiveInt = 4
    aggregate_interval_minutes: PositiveInt = 15
    # Incremental profile-update cadence (phase3-05).
    update_interval_hours: PositiveInt = 24
    min_events_since_last_update: PositiveInt = 5
    max_events_per_update: PositiveInt = 100


class LibrarianTvConfig(BaseModel):
    sampler_episode_count: PositiveInt = 3
    promote_after_n_finished: PositiveInt = 2
    promote_window_days: PositiveInt = 14


class LibrarianConfig(BaseModel):
    max_disk_gb: PositiveInt = 500
    recommendations_ttl_days: PositiveInt = 14
    tv_sampler_ttl_days: PositiveInt = 30
    max_concurrent_downloads: PositiveInt = 2
    max_bytes_in_flight_gb: PositiveInt = 20
    tv: LibrarianTvConfig = Field(default_factory=LibrarianTvConfig)


class RecommendConfig(BaseModel):
    default_n: PositiveInt = 5
    prefilter_k: PositiveInt = 50
    exclude_window_days: PositiveInt = 14
    # Daemon loop: how often to produce a fresh batch.
    interval_hours: PositiveInt = 6


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: PositiveInt = 8787
    # Ceiling for /poster/{id} disk cache. Evicted oldest-accessed
    # first once exceeded (safety net — caching is best-effort).
    poster_cache_size_mb: PositiveInt = 200
    poster_upstream_timeout_s: PositiveInt = 10


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["json", "console"] = "json"


class Config(BaseModel):
    paths: PathsConfig
    jellyfin: JellyfinConfig
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    tmdb: TmdbConfig
    llm: LlmConfig = Field(default_factory=LlmConfig)
    librarian: LibrarianConfig = Field(default_factory=LibrarianConfig)
    taste: TasteConfig = Field(default_factory=TasteConfig)
    recommend: RecommendConfig = Field(default_factory=RecommendConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# --- Env interpolation ---------------------------------------------------------

# ${VAR} or ${VAR:-fallback}. VAR must be [A-Z_][A-Z0-9_]*.
_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(value: Any, path: str) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            fallback = match.group(2)
            env_value = os.environ.get(name)
            if env_value is not None:
                return env_value
            if fallback is not None:
                return fallback
            raise ConfigError(
                f"environment variable {name} not set (referenced in {path or '<root>'})"
            )

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, f"{path}.{k}" if path else k) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


# --- Loader --------------------------------------------------------------------


def _candidate_paths(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit]
    paths: list[Path] = []
    env_path = os.environ.get("ARCHIVE_AGENT_CONFIG")
    if env_path:
        paths.append(Path(env_path))
    paths.append(Path.cwd() / "config.toml")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    xdg_base = Path(xdg) if xdg else Path.home() / ".config"
    paths.append(xdg_base / "archive-agent" / "config.toml")
    paths.append(Path.home() / ".config" / "archive-agent" / "config.toml")
    return paths


def _resolve_path(explicit: Path | None) -> Path:
    checked = _candidate_paths(explicit)
    for p in checked:
        if p.is_file():
            return p
    rendered = "\n  ".join(str(p) for p in checked)
    raise ConfigError(f"config file not found; checked:\n  {rendered}")


def load_config(path: Path | None = None, *, load_env: bool = True) -> Config:
    """Load and validate the config.

    Parameters
    ----------
    path:
        Explicit path to a config.toml. If ``None``, search the standard
        locations (see module docstring).
    load_env:
        If True, load a ``.env`` file from CWD (if present) into the
        process environment before interpolation, without overriding
        existing variables.
    """
    if load_env:
        dotenv_path = Path.cwd() / ".env"
        if dotenv_path.is_file():
            load_dotenv(dotenv_path, override=False)
    config_path = _resolve_path(path)
    with config_path.open("rb") as f:
        raw = tomllib.load(f)
    interpolated = _interpolate(raw, "")
    try:
        return Config.model_validate(interpolated)
    except ValidationError as exc:
        raise ConfigError(f"config validation failed for {config_path}:\n{exc}") from exc


# --- Post-load validator (warnings/errors beyond schema) -----------------------


def validate_config(config: Config) -> tuple[list[str], list[str]]:
    """Return ``(warnings, errors)`` from cross-field and environmental checks.

    Schema validation already happened in ``load_config`` — this layer
    catches things Pydantic can't express: distinct paths, DNS-resolvable
    hostnames, directories that exist on disk, year-range sanity.
    """
    warnings: list[str] = []
    errors: list[str] = []

    media_paths = [
        ("media_movies", config.paths.media_movies),
        ("media_tv", config.paths.media_tv),
        ("media_recommendations", config.paths.media_recommendations),
        ("media_tv_sampler", config.paths.media_tv_sampler),
    ]
    seen: dict[Path, str] = {}
    for name, path in media_paths:
        if path in seen:
            errors.append(f"paths.{name} duplicates paths.{seen[path]} ({path})")
        seen[path] = name

    for label, url in (
        ("jellyfin.url", config.jellyfin.url),
        ("llm.ollama.host", config.llm.ollama.host),
    ):
        parsed = urlparse(url)
        if not parsed.hostname:
            errors.append(f"{label} has no hostname: {url}")
            continue
        try:
            socket.gethostbyname(parsed.hostname)
        except OSError:
            warnings.append(
                f"{label} host {parsed.hostname!r} does not resolve via DNS "
                "(expected if running outside the Docker network)"
            )

    for name, path in media_paths:
        if not path.exists():
            warnings.append(f"paths.{name} does not exist yet: {path}")

    if config.archive.year_from > config.archive.year_to:
        errors.append(
            f"archive.year_from ({config.archive.year_from}) is after "
            f"archive.year_to ({config.archive.year_to})"
        )

    for name in ("nightly_ranking", "profile_update", "nl_search"):
        provider = getattr(config.llm.workflows, name)
        if provider == "claude" and config.llm.claude.api_key is None:
            errors.append(f"llm.workflows.{name} is 'claude' but llm.claude.api_key is unset")

    return warnings, errors
