"""Live Archive.org search. Gated on RUN_INTEGRATION_TESTS=1.

Kept tiny (``limit=3``) so a rerun isn't rude to Archive.org's
infrastructure.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to hit Archive.org",
    ),
]


async def test_moviesandfilms_search_returns_results() -> None:
    from archive_agent.archive.search import search_collection

    count = 0
    async for r in search_collection(
        "moviesandfilms",
        min_downloads=1000,
        year_from=1940,
        year_to=1960,
        limit=3,
    ):
        assert r.identifier
        assert r.title
        count += 1
    assert count > 0


async def test_television_search_returns_results() -> None:
    from archive_agent.archive.search import search_collection

    count = 0
    async for r in search_collection(
        "television",
        min_downloads=500,
        year_from=1950,
        year_to=1970,
        limit=3,
    ):
        assert r.identifier
        count += 1
    assert count > 0
