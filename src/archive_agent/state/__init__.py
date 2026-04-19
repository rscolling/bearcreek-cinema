"""State DB for archive-agent (phase1-03).

Public API:

- ``models`` — Pydantic models that mirror CONTRACTS.md §1.
- ``db.get_db()`` / ``db.connect()`` / ``db.init_db()`` — connection
  lifecycle. No other module opens sqlite3 connections directly.
- ``migrations.apply_pending()`` — run any un-applied migrations.
- ``queries.*`` — per-entity CRUD helpers; nothing outside this package
  should issue raw SQL.
"""

from archive_agent.state.db import close_db, connect, get_db, init_db
from archive_agent.state.migrations import apply_pending, current_version

__all__ = [
    "apply_pending",
    "close_db",
    "connect",
    "current_version",
    "get_db",
    "init_db",
]
