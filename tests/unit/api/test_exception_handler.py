"""Uncaught exceptions must return problem+json — no traceback on the wire."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_unhandled_exception_returns_problem_json(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    # TestClient by default re-raises server exceptions; we need to
    # opt out so the handler's response is surfaced.
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 500
    assert body["title"] == "Internal Server Error"
    # Exception type + message land in `detail`, but nothing else
    # (no stack frames, no file paths).
    assert "RuntimeError" in body["detail"]
    assert "Traceback" not in body["detail"]
    assert "kaboom" in body["detail"]
