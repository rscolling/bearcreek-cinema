"""Parsing + normalization in archive.search — no network."""

from __future__ import annotations

from archive_agent.archive.search import (
    ArchiveSearchResult,
    _build_query,
    _raw_to_result,
    parse_runtime_minutes,
)


def test_parse_runtime_hhmmss() -> None:
    assert parse_runtime_minutes("1:07:39") == 67


def test_parse_runtime_mmss() -> None:
    assert parse_runtime_minutes("25:17") == 25


def test_parse_runtime_approx_minutes() -> None:
    assert parse_runtime_minutes("Approx 30 Minutes") == 30


def test_parse_runtime_plain_minutes() -> None:
    assert parse_runtime_minutes("90 min") == 90
    assert parse_runtime_minutes("90 minutes") == 90


def test_parse_runtime_unparseable_returns_none() -> None:
    assert parse_runtime_minutes("") is None
    assert parse_runtime_minutes(None) is None
    assert parse_runtime_minutes("a short one") is None


def test_parse_runtime_accepts_int() -> None:
    assert parse_runtime_minutes(95) == 95
    assert parse_runtime_minutes(0) is None


def test_raw_to_result_happy_path() -> None:
    raw = {
        "identifier": "mark-of-zorro-1940",
        "title": "The Mark of Zorro",
        "mediatype": "movies",
        "year": 1940,
        "downloads": 26063,
        "runtime": "1:33:44",
        "subject": ["feature film", "drama"],
        "description": "A young Spanish aristocrat...",
        "format": ["h.264", "MP3", "Thumbnail"],
    }
    r = _raw_to_result(raw)
    assert r.identifier == "mark-of-zorro-1940"
    assert r.year == 1940
    assert r.runtime_minutes == 93
    assert r.subject == ["feature film", "drama"]
    assert r.formats == ["h.264", "MP3", "Thumbnail"]


def test_raw_to_result_tolerates_string_subject() -> None:
    """Archive.org sometimes returns `subject` as a bare string."""
    raw = {
        "identifier": "william_tell_spider",
        "title": "William Tell",
        "mediatype": "movies",
        "year": "1959",  # string, not int
        "downloads": 555,
        "subject": "classic tv",  # scalar, not list
    }
    r = _raw_to_result(raw)
    assert r.year == 1959
    assert r.subject == ["classic tv"]


def test_raw_to_result_tolerates_missing_fields() -> None:
    r = _raw_to_result({"identifier": "x", "title": "y"})
    assert r.runtime_minutes is None
    assert r.year is None
    assert r.subject == []
    assert r.formats == []


def test_raw_to_result_year_unparseable() -> None:
    r = _raw_to_result({"identifier": "x", "title": "y", "year": "c. 1940"})
    assert r.year is None


def test_build_query_shape() -> None:
    q = _build_query("moviesandfilms", min_downloads=100, year_from=1920, year_to=2000)
    assert "collection:moviesandfilms" in q
    assert "year:[1920 TO 2000]" in q
    assert "downloads:[100 TO" in q
    assert "mediatype:movies" in q


def test_archive_search_result_extra_ignored() -> None:
    """Upstream fields we don't model mustn't break parsing."""
    r = ArchiveSearchResult.model_validate(
        {
            "identifier": "x",
            "title": "y",
            "future_field_we_do_not_know_about": {"nested": 1},
        }
    )
    assert r.identifier == "x"
