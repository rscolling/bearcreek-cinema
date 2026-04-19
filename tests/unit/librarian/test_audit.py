"""log_action writes to the librarian_actions table correctly."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from archive_agent.librarian.audit import log_action
from archive_agent.librarian.zones import Zone


def test_log_action_basic(db: sqlite3.Connection) -> None:
    row_id = log_action(
        db,
        action="download",
        zone=Zone.RECOMMENDATIONS,
        reason="new candidate download",
        archive_id="sita_sings",
        size_bytes=1234,
    )
    assert row_id >= 1
    row = db.execute(
        "SELECT action, zone, archive_id, show_id, size_bytes, reason, timestamp "
        "FROM librarian_actions WHERE id = ?",
        (row_id,),
    ).fetchone()
    assert row["action"] == "download"
    assert row["zone"] == "recommendations"
    assert row["archive_id"] == "sita_sings"
    assert row["show_id"] is None
    assert row["size_bytes"] == 1234
    assert row["reason"] == "new candidate download"


def test_log_action_timestamp_is_utc(db: sqlite3.Connection) -> None:
    row_id = log_action(db, action="evict", zone=Zone.TV_SAMPLER, reason="30d stale")
    ts = db.execute("SELECT timestamp FROM librarian_actions WHERE id = ?", (row_id,)).fetchone()[
        "timestamp"
    ]
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo == UTC
    # Should be within the last minute
    delta = datetime.now(UTC) - parsed
    assert delta.total_seconds() < 60


def test_log_action_show_id_branch(db: sqlite3.Connection) -> None:
    row_id = log_action(
        db,
        action="promote",
        zone=Zone.TV,
        reason="sampler satisfied",
        show_id="1433",
    )
    row = db.execute(
        "SELECT archive_id, show_id FROM librarian_actions WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["archive_id"] is None
    assert row["show_id"] == "1433"


def test_log_action_rejects_invalid_zone_at_schema_level(db: sqlite3.Connection) -> None:
    """The zone column has no CHECK constraint in the schema — it's free-form
    text. But the Python enum gives us the actual type safety.
    This test just confirms the happy path with all four enum values."""
    for z in Zone:
        log_action(db, action="skip", zone=z, reason="test")
    assert db.execute("SELECT COUNT(*) FROM librarian_actions WHERE action = 'skip'").fetchone()[
        0
    ] == len(list(Zone))


def test_log_action_action_check_constraint(db: sqlite3.Connection) -> None:
    """The schema has CHECK(action IN (...)). Hitting it with raw SQL is
    the most direct way to prove the constraint exists."""
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO librarian_actions (timestamp, action, zone, reason) VALUES (?, ?, ?, ?)",
            ("2026-01-01T00:00:00+00:00", "not-a-real-action", "tv", "x"),
        )
