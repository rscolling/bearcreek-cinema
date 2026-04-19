"""Tests for the TOML config loader + validator (phase1-02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from archive_agent.config import (
    Config,
    ConfigError,
    _interpolate,
    load_config,
    validate_config,
)


def _render(tmp: Path, *, overrides: dict[str, str] | None = None) -> str:
    """Render a minimal valid config TOML with paths rooted at ``tmp``.

    ``overrides`` can replace whole lines (matched by their key-name prefix)
    to test validation failure modes without maintaining a second template.
    """
    tmp_posix = tmp.as_posix()
    lines = [
        "[paths]",
        f'state_db = "{tmp_posix}/state.db"',
        f'media_movies = "{tmp_posix}/movies"',
        f'media_tv = "{tmp_posix}/tv"',
        f'media_recommendations = "{tmp_posix}/rec"',
        f'media_tv_sampler = "{tmp_posix}/sampler"',
        "",
        "[jellyfin]",
        'url = "http://localhost:8096"',
        'api_key = "${TEST_JELLYFIN_KEY}"',
        'user_id = "test-user"',
        "",
        "[tmdb]",
        'api_key = "${TEST_TMDB_KEY}"',
        "",
        "[llm.ollama]",
        'host = "http://localhost:11434"',
        "",
        "[llm.claude]",
        'api_key = "${TEST_ANTHROPIC_KEY:-}"',
    ]
    if overrides:
        for prefix, replacement in overrides.items():
            lines = [replacement if line.startswith(prefix) else line for line in lines]
    return "\n".join(lines) + "\n"


@pytest.fixture
def good_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_JELLYFIN_KEY", "jelly-secret")
    monkeypatch.setenv("TEST_TMDB_KEY", "tmdb-secret")
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("ARCHIVE_AGENT_CONFIG", raising=False)


def test_loads_from_explicit_path(tmp_path: Path, good_env: None) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path))
    cfg = load_config(cfg_path, load_env=False)
    assert isinstance(cfg, Config)
    assert cfg.jellyfin.url == "http://localhost:8096"
    assert cfg.jellyfin.api_key.get_secret_value() == "jelly-secret"
    assert cfg.tmdb.api_key.get_secret_value() == "tmdb-secret"
    assert cfg.llm.claude.api_key is None  # fallback-to-empty coerced to None
    assert cfg.librarian.max_disk_gb == 500  # default when section omitted


def test_missing_env_var_raises_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path))
    monkeypatch.delenv("TEST_JELLYFIN_KEY", raising=False)
    monkeypatch.setenv("TEST_TMDB_KEY", "tmdb-secret")
    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_path, load_env=False)
    msg = str(exc_info.value)
    assert "TEST_JELLYFIN_KEY" in msg
    assert "jellyfin.api_key" in msg


def test_env_fallback_syntax(tmp_path: Path, good_env: None) -> None:
    # TEST_ANTHROPIC_KEY is unset but ${VAR:-} fallback makes it empty, not error
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path))
    cfg = load_config(cfg_path, load_env=False)
    assert cfg.llm.claude.api_key is None


def test_file_not_found_lists_all_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCHIVE_AGENT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc_info:
        load_config(load_env=False)
    msg = str(exc_info.value)
    assert "config.toml" in msg
    # Should list at least the CWD candidate
    assert str(tmp_path / "config.toml") in msg


def test_explicit_path_wins_over_env(
    tmp_path: Path, good_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    good_path = tmp_path / "good.toml"
    good_path.write_text(_render(tmp_path))
    bad_path = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("ARCHIVE_AGENT_CONFIG", str(bad_path))
    cfg = load_config(good_path, load_env=False)
    assert cfg.jellyfin.user_id == "test-user"


def test_secrets_redacted_in_model_dump_json(tmp_path: Path, good_env: None) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path))
    cfg = load_config(cfg_path, load_env=False)
    dumped = cfg.model_dump_json(indent=2)
    assert "jelly-secret" not in dumped
    assert "tmdb-secret" not in dumped
    assert "**********" in dumped


def test_validate_warns_on_missing_media_paths(tmp_path: Path, good_env: None) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path))
    cfg = load_config(cfg_path, load_env=False)
    warnings, errors = validate_config(cfg)
    # media_* dirs don't exist yet
    assert any("media_movies" in w for w in warnings)
    assert errors == []


def test_validate_rejects_duplicate_media_paths(tmp_path: Path, good_env: None) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        _render(
            tmp_path,
            overrides={"media_tv ": f'media_tv = "{tmp_path.as_posix()}/movies"'},
        )
    )
    cfg = load_config(cfg_path, load_env=False)
    _, errors = validate_config(cfg)
    assert any("duplicates" in e for e in errors)


def test_validate_rejects_reversed_year_range(tmp_path: Path, good_env: None) -> None:
    # Append an archive section with reversed years
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path) + "\n[archive]\nyear_from = 2000\nyear_to = 1920\n")
    cfg = load_config(cfg_path, load_env=False)
    _, errors = validate_config(cfg)
    assert any("year_from" in e and "year_to" in e for e in errors)


def test_validate_rejects_claude_without_key(tmp_path: Path, good_env: None) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_render(tmp_path) + '\n[llm.workflows]\nnightly_ranking = "claude"\n')
    cfg = load_config(cfg_path, load_env=False)
    _, errors = validate_config(cfg)
    assert any("nightly_ranking" in e and "claude" in e for e in errors)


def test_interpolate_is_recursive() -> None:
    import os

    os.environ["X"] = "hello"
    try:
        assert _interpolate({"a": {"b": "${X}"}}, "") == {"a": {"b": "hello"}}
        assert _interpolate(["${X}", "literal"], "") == ["hello", "literal"]
    finally:
        del os.environ["X"]


def test_interpolate_only_affects_strings() -> None:
    # Integers should pass through untouched
    assert _interpolate(42, "foo") == 42
    assert _interpolate(3.14, "bar") == 3.14
    assert _interpolate(True, "baz") is True
