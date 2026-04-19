"""Disk-usage accounting + budget math.

``max_disk_gb`` applies to agent-managed zones only; ``/media/movies``
is outside the budget entirely. ``scan_zone`` is defensive — missing
directories and permission errors don't raise, they just contribute
zero bytes (see the note about Jellyfin-container-UID files in
phase2-05's task card).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from archive_agent.config import Config
from archive_agent.librarian.zones import AGENT_MANAGED, Zone, zone_path
from archive_agent.logging import get_logger

__all__ = ["BudgetReport", "ZoneUsage", "budget_report", "scan_zone"]

log = get_logger("archive_agent.librarian.budget")

_BYTES_PER_GB = 1_000_000_000  # decimal GB, matches disk-vendor labels + health all


class ZoneUsage(BaseModel):
    zone: Zone
    path: Path
    used_bytes: int
    file_count: int


class BudgetReport(BaseModel):
    zones: list[ZoneUsage]
    agent_used_bytes: int  # sum of AGENT_MANAGED zones only
    budget_bytes: int  # max_disk_gb * 1e9
    headroom_bytes: int  # budget_bytes - agent_used_bytes
    over_budget: bool


def scan_zone(path: Path, *, zone: Zone | None = None) -> ZoneUsage:
    """Recursive walk returning total file bytes + count.

    Never raises — missing paths count as empty, permission errors on
    individual files log a warning and contribute zero bytes.
    """
    if not path.exists():
        return ZoneUsage(
            zone=zone or Zone.RECOMMENDATIONS,  # placeholder; caller sets real zone
            path=path,
            used_bytes=0,
            file_count=0,
        )
    total = 0
    count = 0
    for root, _dirs, files in os.walk(
        path, onerror=lambda e: log.warning("walk_error", error=str(e))
    ):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat().st_size
                count += 1
            except OSError as exc:
                log.warning("stat_error", path=str(fp), error=str(exc))
    return ZoneUsage(
        zone=zone or Zone.RECOMMENDATIONS,
        path=path,
        used_bytes=total,
        file_count=count,
    )


def budget_report(config: Config) -> BudgetReport:
    """Scan every zone and produce a consolidated report."""
    usages: list[ZoneUsage] = []
    for z in Zone:
        usage = scan_zone(zone_path(z, config), zone=z)
        usages.append(usage)
    agent_used = sum(u.used_bytes for u in usages if u.zone in AGENT_MANAGED)
    budget_bytes = config.librarian.max_disk_gb * _BYTES_PER_GB
    return BudgetReport(
        zones=usages,
        agent_used_bytes=agent_used,
        budget_bytes=budget_bytes,
        headroom_bytes=budget_bytes - agent_used,
        over_budget=agent_used > budget_bytes,
    )
