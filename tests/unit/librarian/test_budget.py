"""scan_zone + budget_report."""

from __future__ import annotations

from pathlib import Path

from archive_agent.config import Config
from archive_agent.librarian.budget import _BYTES_PER_GB, budget_report, scan_zone
from archive_agent.librarian.zones import AGENT_MANAGED, Zone


def _write(path: Path, size_bytes: int) -> None:
    path.write_bytes(b"\x00" * size_bytes)


def test_scan_zone_counts_files_and_bytes(tmp_path: Path) -> None:
    _write(tmp_path / "a.mp4", 1000)
    _write(tmp_path / "b.mp4", 2000)
    (tmp_path / "nested").mkdir()
    _write(tmp_path / "nested" / "c.mp4", 500)
    usage = scan_zone(tmp_path, zone=Zone.RECOMMENDATIONS)
    assert usage.used_bytes == 3500
    assert usage.file_count == 3
    assert usage.zone == Zone.RECOMMENDATIONS
    assert usage.path == tmp_path


def test_scan_zone_missing_path_is_zero(tmp_path: Path) -> None:
    usage = scan_zone(tmp_path / "does-not-exist", zone=Zone.TV)
    assert usage.used_bytes == 0
    assert usage.file_count == 0
    assert usage.zone == Zone.TV


def test_scan_zone_empty_dir_is_zero(tmp_path: Path) -> None:
    usage = scan_zone(tmp_path, zone=Zone.TV_SAMPLER)
    assert usage.used_bytes == 0
    assert usage.file_count == 0


def test_budget_report_sums_agent_managed_only(config: Config) -> None:
    """/media/movies is USER_OWNED and should NOT count against the budget."""
    _write(config.paths.media_movies / "user.mp4", 10_000_000)  # 10 MB
    _write(config.paths.media_tv / "show.mp4", 5_000_000)  # 5 MB
    _write(config.paths.media_recommendations / "rec.mp4", 3_000_000)  # 3 MB
    _write(config.paths.media_tv_sampler / "sampler.mp4", 2_000_000)  # 2 MB
    report = budget_report(config)
    assert report.agent_used_bytes == 10_000_000  # 5+3+2, NOT 20
    # budget_bytes = max_disk_gb (10 in fixture) * 1e9
    assert report.budget_bytes == 10 * _BYTES_PER_GB
    assert report.headroom_bytes == report.budget_bytes - report.agent_used_bytes
    assert report.over_budget is False
    # All four zones are represented
    zones_reported = {u.zone for u in report.zones}
    assert zones_reported == set(Zone)


def test_budget_report_flags_over_budget(config: Config) -> None:
    config.librarian.max_disk_gb = 1  # 1 GB budget
    # Write 1.5 GB into tv (agent-managed)
    _write(config.paths.media_tv / "big.mp4", int(1.5 * _BYTES_PER_GB))
    report = budget_report(config)
    assert report.over_budget is True
    assert report.headroom_bytes < 0


def test_budget_report_ok_when_paths_missing(tmp_path: Path, config: Config) -> None:
    """Even if a zone path doesn't exist, budget_report returns cleanly."""
    (config.paths.media_tv).rmdir()  # remove one zone dir
    report = budget_report(config)
    # Zone still reported, just with zero usage
    tv_row = next(u for u in report.zones if u.zone == Zone.TV)
    assert tv_row.used_bytes == 0
    assert report.over_budget is False


def test_budget_report_contains_all_agent_managed_and_movies(config: Config) -> None:
    """Regression on the set membership — the report must list all four."""
    report = budget_report(config)
    listed = {u.zone for u in report.zones}
    assert listed >= AGENT_MANAGED
    assert Zone.MOVIES in listed
