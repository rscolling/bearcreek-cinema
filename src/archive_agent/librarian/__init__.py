"""Librarian subsystem: zones, budget, placement (phase2-06),
eviction (phase2-07), TV sampler (phase2-08), audit trail.

Module boundary (GUARDRAILS.md): the librarian is the **only** module
that moves, promotes, or deletes files under ``/media/*``. Callers
request actions — they never ``shutil.move`` on their own.
"""

from archive_agent.librarian.audit import LibrarianAction, log_action
from archive_agent.librarian.budget import BudgetReport, ZoneUsage, budget_report, scan_zone
from archive_agent.librarian.placement import (
    BudgetExceededError,
    PlacementError,
    PlaceResult,
    place,
    promote_movie,
    promote_show,
)
from archive_agent.librarian.zones import AGENT_MANAGED, USER_OWNED, Zone, zone_path

__all__ = [
    "AGENT_MANAGED",
    "USER_OWNED",
    "BudgetExceededError",
    "BudgetReport",
    "LibrarianAction",
    "PlaceResult",
    "PlacementError",
    "Zone",
    "ZoneUsage",
    "budget_report",
    "log_action",
    "place",
    "promote_movie",
    "promote_show",
    "scan_zone",
    "zone_path",
]
