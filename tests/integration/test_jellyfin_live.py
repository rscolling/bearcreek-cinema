"""Live round-trip against the real Jellyfin server. Gated on RUN_INTEGRATION_TESTS=1.

These tests read the agent's own ``.env`` + ``config.toml`` so they
exercise the same code path the CLI uses. They make no writes to
Jellyfin — only GETs.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to hit the real Jellyfin server",
    ),
]


@pytest.fixture
def cfg():  # type: ignore[no-untyped-def]
    from archive_agent.config import load_config

    return load_config()


async def test_ping_returns_server_info(cfg) -> None:  # type: ignore[no-untyped-def]
    from archive_agent.jellyfin.client import JellyfinClient

    async with JellyfinClient(
        cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
    ) as client:
        info = await client.ping()
        assert info.server_name
        assert info.version


async def test_list_libraries_nonempty(cfg) -> None:  # type: ignore[no-untyped-def]
    from archive_agent.jellyfin.client import JellyfinClient

    async with JellyfinClient(
        cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
    ) as client:
        libs = await client.list_libraries()
        assert libs, "user should see at least one library"


async def test_configured_user_resolves(cfg) -> None:  # type: ignore[no-untyped-def]
    from archive_agent.jellyfin.client import JellyfinClient

    async with JellyfinClient(
        cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
    ) as client:
        user = await client.get_user()
        assert user.id == cfg.jellyfin.user_id
