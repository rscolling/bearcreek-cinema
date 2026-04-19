"""Jellyfin placement (phase2-09): library resolution + item matching
+ scan-and-resolve."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from archive_agent.jellyfin.models import JellyfinItem, JellyfinItemPage
from archive_agent.jellyfin.placement import (
    LibraryMap,
    MissingLibraryError,
    _find_item_for_candidate,
    _match_zone_to_folders,
    _normalize_path,
    _titles_match,
    resolve_libraries,
    scan_and_resolve,
)
from archive_agent.librarian.zones import Zone
from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending
from archive_agent.state.models import Candidate, CandidateStatus, ContentType
from archive_agent.state.queries import candidates as q_candidates


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


# --- path normalization ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/media/movies", "/media/movies"),
        ("/media/movies/", "/media/movies"),
        ("/media/movies//", "/media/movies"),
        ("\\media\\movies", "/media/movies"),  # Windows-style from an old API
        ("/MEDIA/Movies", "/media/movies"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert _normalize_path(raw) == expected


# --- library mapping ---


_FULL_VFOLDERS = [
    {"Name": "Movies", "ItemId": "lib-movies", "Locations": ["/media/movies"]},
    {"Name": "Shows", "ItemId": "lib-tv", "Locations": ["/media/tv"]},
    {"Name": "Recommendations", "ItemId": "lib-rec", "Locations": ["/media/recommendations/"]},
    {"Name": "TV Sampler", "ItemId": "lib-sampler", "Locations": ["/media/tv-sampler"]},
    # Extra user library the agent doesn't care about
    {"Name": "Books", "ItemId": "lib-books", "Locations": ["/media/books"]},
]


def test_match_zone_to_folders_all_present() -> None:
    got = _match_zone_to_folders(_FULL_VFOLDERS)
    assert got == {
        Zone.MOVIES: "lib-movies",
        Zone.TV: "lib-tv",
        Zone.RECOMMENDATIONS: "lib-rec",
        Zone.TV_SAMPLER: "lib-sampler",
    }


def test_match_zone_prefers_itemid_but_accepts_id() -> None:
    """Older Jellyfin versions return ``Id`` instead of ``ItemId``."""
    folders = [
        {"Name": "Movies", "Id": "lib-movies", "Locations": ["/media/movies"]},
        {"Name": "Shows", "ItemId": "lib-tv", "Locations": ["/media/tv"]},
        {"Name": "Recommendations", "ItemId": "lib-rec", "Locations": ["/media/recommendations"]},
        {"Name": "TV Sampler", "ItemId": "lib-sampler", "Locations": ["/media/tv-sampler"]},
    ]
    got = _match_zone_to_folders(folders)
    assert got[Zone.MOVIES] == "lib-movies"


def test_match_zone_ignores_missing_itemid() -> None:
    folders = [
        {"Name": "Empty", "Locations": ["/media/movies"]},  # no id
    ]
    assert _match_zone_to_folders(folders) == {}


async def test_resolve_libraries_happy_path(db: sqlite3.Connection) -> None:
    client = AsyncMock()
    client.raw_get = AsyncMock(return_value=_FULL_VFOLDERS)
    libs = await resolve_libraries(client)
    assert isinstance(libs, LibraryMap)
    assert libs.movies == "lib-movies"
    assert libs.recommendations == "lib-rec"
    assert libs.library_id(Zone.TV_SAMPLER) == "lib-sampler"
    client.raw_get.assert_called_once_with("/Library/VirtualFolders")


async def test_resolve_libraries_missing_raises() -> None:
    """Dropping one of the four expected zones must surface loudly —
    silently omitting means the agent looks fine but fails at
    scan-and-resolve time for that zone."""
    client = AsyncMock()
    client.raw_get = AsyncMock(
        return_value=[
            {"Name": "Movies", "ItemId": "lib-movies", "Locations": ["/media/movies"]},
            {"Name": "Shows", "ItemId": "lib-tv", "Locations": ["/media/tv"]},
            {"Name": "TV Sampler", "ItemId": "lib-sampler", "Locations": ["/media/tv-sampler"]},
            # Recommendations is deliberately missing
        ]
    )
    with pytest.raises(MissingLibraryError) as exc:
        await resolve_libraries(client)
    assert "/media/recommendations" in str(exc.value)
    assert "Dashboard" in str(exc.value)  # the help hint


async def test_resolve_libraries_tolerates_dict_response() -> None:
    """Some Jellyfin versions wrap the folders list differently; our
    parser normalizes both shapes."""
    client = AsyncMock()
    client.raw_get = AsyncMock(return_value={})  # degenerate — no folders
    with pytest.raises(MissingLibraryError):
        await resolve_libraries(client)


# --- _find_item_for_candidate ---


def _item(
    id: str,
    *,
    name: str,
    type_: str = "Movie",
    year: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    series: str | None = None,
) -> JellyfinItem:
    return JellyfinItem(
        Id=id,
        Name=name,
        Type=type_,
        ProductionYear=year,
        ParentIndexNumber=season,
        IndexNumber=episode,
        SeriesName=series,
    )


def _page(items: list[JellyfinItem]) -> JellyfinItemPage:
    return JellyfinItemPage(Items=items, TotalRecordCount=len(items))  # type: ignore[call-arg]


def _movie_candidate(title: str = "Sita Sings the Blues", year: int | None = 2008) -> Candidate:
    return Candidate(
        archive_id="sita_sings",
        content_type=ContentType.MOVIE,
        title=title,
        year=year,
        source_collection="moviesandfilms",
        status=CandidateStatus.DOWNLOADED,
        discovered_at=datetime.now(UTC),
    )


def _episode_candidate(
    archive_id: str = "dvds-s01e03",
    *,
    season: int = 1,
    episode: int = 3,
) -> Candidate:
    return Candidate(
        archive_id=archive_id,
        content_type=ContentType.EPISODE,
        title="Sick Boy and Sore Loser",
        show_id="1433",
        season=season,
        episode=episode,
        source_collection="television",
        status=CandidateStatus.SAMPLING,
        discovered_at=datetime.now(UTC),
    )


def test_titles_match_case_and_whitespace_insensitive() -> None:
    assert _titles_match("Sita Sings the Blues", "sita  sings the blues")
    assert _titles_match("The Third Man", "THE THIRD MAN")
    assert not _titles_match("The Third Man", "The Fourth Man")


async def test_find_movie_by_title_and_year() -> None:
    client = AsyncMock()
    client.list_items = AsyncMock(
        return_value=_page(
            [
                _item("id1", name="Sita Sings the Blues", year=2008),
                _item("id2", name="Another Movie", year=1969),
            ]
        )
    )
    got = await _find_item_for_candidate(client, "lib-rec", _movie_candidate())
    assert got is not None
    assert got.id == "id1"


async def test_find_movie_disambiguates_on_year() -> None:
    """The Lost World (1925) vs (1960) — year is the tiebreaker."""
    client = AsyncMock()
    client.list_items = AsyncMock(
        return_value=_page(
            [
                _item("older", name="The Lost World", year=1925),
                _item("newer", name="The Lost World", year=1960),
            ]
        )
    )
    cand = _movie_candidate(title="The Lost World", year=1960)
    got = await _find_item_for_candidate(client, "lib-movies", cand)
    assert got is not None
    assert got.id == "newer"


async def test_find_movie_no_match_returns_none() -> None:
    client = AsyncMock()
    client.list_items = AsyncMock(
        return_value=_page([_item("x", name="Something Else", year=1950)])
    )
    got = await _find_item_for_candidate(client, "lib-rec", _movie_candidate())
    assert got is None


async def test_find_episode_by_season_and_episode() -> None:
    client = AsyncMock()
    client.list_items = AsyncMock(
        return_value=_page(
            [
                _item("e1", name="Ep 1", type_="Episode", season=1, episode=1, series="DVDS"),
                _item("e3", name="Ep 3", type_="Episode", season=1, episode=3, series="DVDS"),
                _item("e5", name="Ep 5", type_="Episode", season=1, episode=5, series="DVDS"),
            ]
        )
    )
    got = await _find_item_for_candidate(client, "lib-sampler", _episode_candidate())
    assert got is not None
    assert got.id == "e3"


# --- scan_and_resolve ---


async def test_scan_and_resolve_persists_item_id(db: sqlite3.Connection) -> None:
    q_candidates.upsert_candidate(db, _movie_candidate())
    client = AsyncMock()
    client.raw_get = AsyncMock(return_value=_FULL_VFOLDERS)
    client.trigger_library_scan = AsyncMock()
    client.list_items = AsyncMock(
        return_value=_page([_item("jf-item-1", name="Sita Sings the Blues", year=2008)])
    )

    item_id = await scan_and_resolve(
        client,
        db,
        archive_id="sita_sings",
        zone=Zone.RECOMMENDATIONS,
        timeout_s=5,
    )
    assert item_id == "jf-item-1"
    client.trigger_library_scan.assert_awaited_once_with("lib-rec")

    after = q_candidates.get_by_archive_id(db, "sita_sings")
    assert after is not None
    assert after.jellyfin_item_id == "jf-item-1"


async def test_scan_and_resolve_returns_none_on_timeout(
    db: sqlite3.Connection,
) -> None:
    q_candidates.upsert_candidate(db, _movie_candidate())
    client = AsyncMock()
    client.raw_get = AsyncMock(return_value=_FULL_VFOLDERS)
    client.trigger_library_scan = AsyncMock()
    client.list_items = AsyncMock(return_value=_page([]))  # never indexed

    item_id = await scan_and_resolve(
        client,
        db,
        archive_id="sita_sings",
        zone=Zone.RECOMMENDATIONS,
        timeout_s=1,
        poll_interval_s=0.3,
    )
    assert item_id is None
    # Candidate's jellyfin_item_id unchanged
    after = q_candidates.get_by_archive_id(db, "sita_sings")
    assert after is not None
    assert after.jellyfin_item_id is None


async def test_scan_and_resolve_unknown_archive_id_raises(
    db: sqlite3.Connection,
) -> None:
    client = AsyncMock()
    with pytest.raises(ValueError, match="no candidate"):
        await scan_and_resolve(
            client,
            db,
            archive_id="does_not_exist",
            zone=Zone.RECOMMENDATIONS,
            timeout_s=1,
        )
