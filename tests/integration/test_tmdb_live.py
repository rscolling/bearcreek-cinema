"""Live TMDb round-trip. Gated on RUN_INTEGRATION_TESTS=1.

Verifies the agent's TMDb key, the ``/configuration`` cache path,
and a simple movie search. Kept small so rerunning is cheap.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to hit TMDb",
    ),
]


async def test_configuration_returns_secure_base_url() -> None:
    from archive_agent.config import load_config
    from archive_agent.metadata import TmdbClient
    from archive_agent.state.db import connect
    from archive_agent.state.migrations import apply_pending

    cfg = load_config()
    conn = connect(":memory:")
    apply_pending(conn)

    async with TmdbClient(cfg.tmdb.api_key, conn) as client:
        config = await client.configuration()
        assert config.images_base_url.startswith("https://image.tmdb.org")
        assert "w342" in config.poster_sizes


async def test_search_movie_public_domain_title() -> None:
    from archive_agent.config import load_config
    from archive_agent.metadata import TmdbClient
    from archive_agent.state.db import connect
    from archive_agent.state.migrations import apply_pending

    cfg = load_config()
    conn = connect(":memory:")
    apply_pending(conn)

    async with TmdbClient(cfg.tmdb.api_key, conn) as client:
        result = await client.search_movie("Night of the Living Dead", 1968)
        assert result is not None
        assert "Night of the Living Dead" in result.title
        assert result.year == 1968
