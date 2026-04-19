"""Jellyfin-compatible naming + filesystem-safe sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest

from archive_agent.librarian.naming import (
    disambiguate,
    disambiguate_folder,
    jellyfin_episode_filename,
    jellyfin_movie_filename,
    jellyfin_movie_folder,
    jellyfin_season_folder,
    jellyfin_show_folder,
    sanitize_filename,
)

# --- sanitize ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Clean Title", "Clean Title"),
        ('Title with "quotes" and colons:', "Title with quotes and colons"),
        ("Title/with\\slashes", "Title with slashes"),
        ("Title<with>angle|brackets", "Title with angle brackets"),
        ("Title*with?glob", "Title with glob"),
        ("Multiple     spaces", "Multiple spaces"),
        ("  leading and trailing  ", "leading and trailing"),
        ("trailing dots...", "trailing dots"),
        ("", ""),
        ("\x01control\x1fchars", "control chars"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


# --- movies --------------------------------------------------------------


def test_movie_folder_with_year() -> None:
    assert jellyfin_movie_folder("Sita Sings the Blues", 2008) == "Sita Sings the Blues (2008)"


def test_movie_folder_without_year() -> None:
    assert jellyfin_movie_folder("Undated Short", None) == "Undated Short"


def test_movie_folder_empty_title() -> None:
    assert jellyfin_movie_folder("", 1950) == "Unknown Title (1950)"


def test_movie_folder_sanitizes_title() -> None:
    assert jellyfin_movie_folder("M*A*S*H: The Film", 1970) == "M A S H The Film (1970)"


def test_movie_filename_uses_folder_stem() -> None:
    assert (
        jellyfin_movie_filename("Sita Sings the Blues", 2008, ".mp4")
        == "Sita Sings the Blues (2008).mp4"
    )


# --- shows + episodes ---------------------------------------------------


def test_show_folder_sanitizes() -> None:
    assert jellyfin_show_folder("Dick Van Dyke / Show") == "Dick Van Dyke Show"


def test_show_folder_empty() -> None:
    assert jellyfin_show_folder("") == "Unknown Show"


def test_season_folder_padding() -> None:
    assert jellyfin_season_folder(1) == "Season 01"
    assert jellyfin_season_folder(12) == "Season 12"


def test_episode_filename_full_shape() -> None:
    got = jellyfin_episode_filename(
        "The Dick Van Dyke Show", 1, 3, "Sick Boy and Sore Loser", ".mp4"
    )
    assert got == "The Dick Van Dyke Show - S01E03 - Sick Boy and Sore Loser.mp4"


def test_episode_filename_without_episode_title() -> None:
    got = jellyfin_episode_filename("Rainbow Quest", 1, 14, None, ".mp4")
    assert got == "Rainbow Quest - S01E14.mp4"


def test_episode_filename_empty_ep_title_same_as_none() -> None:
    got = jellyfin_episode_filename("Show", 1, 2, "", ".mkv")
    assert got == "Show - S01E02.mkv"


def test_episode_filename_suppresses_when_ep_title_equals_show() -> None:
    """Some IA items use the show name as episode title — drop the dup."""
    got = jellyfin_episode_filename("Some Show", 1, 1, "some show", ".mp4")
    assert got == "Some Show - S01E01.mp4"


def test_episode_filename_sanitizes() -> None:
    got = jellyfin_episode_filename("Show", 2, 10, 'The "Crazy" Episode: A Story', ".mp4")
    assert got == "Show - S02E10 - The Crazy Episode A Story.mp4"


# --- disambiguate -------------------------------------------------------


def test_disambiguate_when_free(tmp_path: Path) -> None:
    dest = tmp_path / "Movie.mp4"
    assert disambiguate(dest) == dest


def test_disambiguate_appends_n(tmp_path: Path) -> None:
    dest = tmp_path / "Movie.mp4"
    dest.write_bytes(b"x")
    (tmp_path / "Movie (1).mp4").write_bytes(b"x")
    got = disambiguate(dest)
    assert got == tmp_path / "Movie (2).mp4"


def test_disambiguate_folder_when_free(tmp_path: Path) -> None:
    dest = tmp_path / "Movie (2008)"
    assert disambiguate_folder(dest) == dest


def test_disambiguate_folder_appends_n(tmp_path: Path) -> None:
    dest = tmp_path / "Movie (2008)"
    dest.mkdir()
    got = disambiguate_folder(dest)
    assert got == tmp_path / "Movie (2008) (1)"


def test_disambiguate_raises_when_exhausted(tmp_path: Path) -> None:
    dest = tmp_path / "M.mp4"
    dest.write_bytes(b"x")
    for n in range(1, 4):
        (tmp_path / f"M ({n}).mp4").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        disambiguate(dest, max_attempts=3)
