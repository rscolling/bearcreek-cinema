"""FastAPI scaffold smoke tests — app builds, root responds, lifespan
wires DB + provider onto app.state."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_create_app_returns_fastapi(app: FastAPI) -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "Bear Creek Cinema"


def test_root_returns_alive(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {
        "name": "bear-creek-cinema-agent",
        "status": "alive",
    }


def test_root_response_has_request_id_header(client: TestClient) -> None:
    resp = client.get("/")
    assert any(k.lower() == "x-request-id" for k in resp.headers)


def test_lifespan_wires_state(app: FastAPI) -> None:
    """Lifespan populates app.state.db + app.state.provider."""
    with TestClient(app):
        assert app.state.db is not None
        assert app.state.provider is not None
        # DB should have migrations applied — schema_version table exists.
        cur = app.state.db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_version'"
        )
        assert cur.fetchone() is not None
