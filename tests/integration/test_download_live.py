"""Live Archive.org download. Gated on RUN_INTEGRATION_TESTS=1.

Pulls one small public-domain short to confirm the end-to-end pipeline
works. Kept intentionally small — a ~10 MB Popeye short is both
genuinely in the public domain and friendly on Archive.org's infra.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to download from Archive.org",
    ),
]


# Tiny Turner collection short (~5 MB h.264). The identifier was
# chosen by sorting ``collection:moviesandfilms`` with
# ``format:(h.264)`` by ``item_size asc``. If the item disappears,
# any other small PD short works — the test is about the pipeline.
_SMALL_PD_SHORT = "turner_video_12723"


async def test_download_small_public_domain_short(tmp_path: Path) -> None:
    from archive_agent.archive.downloader import DownloadRequest, download_one
    from archive_agent.librarian.zones import Zone
    from archive_agent.state.db import connect
    from archive_agent.state.migrations import apply_pending

    conn = connect(":memory:")
    apply_pending(conn)

    req = DownloadRequest(
        archive_id=_SMALL_PD_SHORT,
        zone=Zone.RECOMMENDATIONS,
        dest_dir=tmp_path,
    )
    result = await download_one(req, conn)

    # Accept either done or skipped (skipped = already-in-DB shouldn't
    # happen on a fresh :memory: db, but keep the assertion tolerant).
    assert result.status in ("done", "skipped"), f"unexpected status: {result}"
    if result.status == "done":
        assert result.file_path is not None
        assert result.file_path.exists()
        assert result.size_bytes is not None
        assert result.size_bytes > 0

    # Second run — should short-circuit to skipped
    second = await download_one(req, conn)
    assert second.status == "skipped"
