"""/health endpoint + subsystem rollup semantics.

Subsystems (Ollama, Jellyfin, Claude) reach real services if we let
them; we monkey-patch the probe functions so tests stay offline.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_agent.api import subsystems as subsystems_mod
from archive_agent.api.subsystems import _rollup  # type: ignore[attr-defined]


def _patch_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ollama: dict[str, Any] | None = None,
    jellyfin: dict[str, Any] | None = None,
    claude: dict[str, Any] | None = None,
) -> None:
    async def _ollama(*_: Any, **__: Any) -> dict[str, Any]:
        return ollama or {"status": "ok", "detail": "stubbed"}

    async def _jellyfin(*_: Any, **__: Any) -> dict[str, Any]:
        return jellyfin or {"status": "ok", "version": "10.9.0"}

    async def _claude(*_: Any, **__: Any) -> dict[str, Any] | None:
        return claude  # None by default

    monkeypatch.setattr(subsystems_mod, "_probe_ollama", _ollama)
    monkeypatch.setattr(subsystems_mod, "_probe_jellyfin", _jellyfin)
    monkeypatch.setattr(subsystems_mod, "_probe_claude", _claude)


# --- endpoint --------------------------------------------------------------


def test_health_returns_ok_when_all_subsystems_ok(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probes(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ollama"]["status"] == "ok"
    assert body["jellyfin"]["status"] == "ok"
    assert body["state_db"]["status"] == "ok"
    assert body["disk"]["status"] == "ok"
    # Claude not configured in the fixture → omitted from the body.
    assert body.get("claude") is None


def test_health_returns_down_when_ollama_down(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probes(monkeypatch, ollama={"status": "down", "detail": "connection refused"})
    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200  # 200 even when degraded — body carries status
    body = resp.json()
    assert body["status"] == "down"
    assert body["ollama"]["status"] == "down"


def test_health_returns_degraded_when_ollama_degraded(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probes(monkeypatch, ollama={"status": "degraded", "detail": "smoke test"})
    with TestClient(app) as client:
        resp = client.get("/health")

    body = resp.json()
    assert body["status"] == "degraded"


def test_health_surfaces_claude_when_configured(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probes(monkeypatch, claude={"status": "ok", "detail": "smoke"})
    with TestClient(app) as client:
        resp = client.get("/health")

    body = resp.json()
    assert body["claude"] is not None
    assert body["claude"]["status"] == "ok"


def test_health_down_claude_flips_aggregate(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probes(monkeypatch, claude={"status": "down", "detail": "bad key"})
    with TestClient(app) as client:
        resp = client.get("/health")

    body = resp.json()
    assert body["status"] == "down"


# --- rollup decision table ------------------------------------------------


def test_rollup_all_ok_is_ok() -> None:
    assert _rollup({"status": "ok"}, {"status": "ok"}) == "ok"


def test_rollup_any_down_is_down() -> None:
    assert _rollup({"status": "ok"}, {"status": "down"}) == "down"


def test_rollup_any_degraded_when_no_down_is_degraded() -> None:
    assert _rollup({"status": "ok"}, {"status": "degraded"}) == "degraded"


def test_rollup_down_beats_degraded() -> None:
    assert _rollup({"status": "degraded"}, {"status": "down"}) == "down"


def test_rollup_ignores_none_entries() -> None:
    assert _rollup({"status": "ok"}, None, {"status": "ok"}) == "ok"
