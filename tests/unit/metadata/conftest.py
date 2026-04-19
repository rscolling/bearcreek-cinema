"""Fixtures for metadata/ tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from archive_agent.state.db import connect
from archive_agent.state.migrations import apply_pending


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_pending(conn)
    yield conn
    conn.close()


# A representative TMDb /search/movie response for Sita Sings the Blues
SEARCH_MOVIE_RESPONSE = {
    "page": 1,
    "total_results": 1,
    "total_pages": 1,
    "results": [
        {
            "id": 22660,
            "title": "Sita Sings the Blues",
            "original_title": "Sita Sings the Blues",
            "release_date": "2008-12-25",
            "overview": "A musical retelling of the Ramayana.",
            "poster_path": "/abc123.jpg",
            "genre_ids": [16, 10402],  # Animation, Music
            "popularity": 4.2,
            "vote_average": 7.6,
        }
    ],
}

GET_MOVIE_RESPONSE = {
    "id": 22660,
    "title": "Sita Sings the Blues",
    "release_date": "2008-12-25",
    "runtime": 82,
    "overview": "A detailed retelling of the Ramayana, set to 1920s blues.",
    "poster_path": "/abc123.jpg",
    "genres": [
        {"id": 16, "name": "Animation"},
        {"id": 10402, "name": "Music"},
    ],
}

CONFIGURATION_RESPONSE = {
    "images": {
        "base_url": "http://image.tmdb.org/t/p/",
        "secure_base_url": "https://image.tmdb.org/t/p/",
        "backdrop_sizes": ["w300", "w780", "w1280", "original"],
        "poster_sizes": ["w92", "w154", "w185", "w342", "w500", "w780", "original"],
    },
    "change_keys": [],
}

GENRES_MOVIE_RESPONSE = {
    "genres": [
        {"id": 16, "name": "Animation"},
        {"id": 35, "name": "Comedy"},
        {"id": 10402, "name": "Music"},
    ]
}

SEARCH_TV_RESPONSE = {
    "page": 1,
    "total_results": 1,
    "total_pages": 1,
    "results": [
        {
            "id": 1433,
            "name": "The Dick Van Dyke Show",
            "first_air_date": "1961-10-03",
            "overview": "Mid-century sitcom about a comedy writer.",
            "poster_path": "/dvds.jpg",
            "genre_ids": [35],
        }
    ],
}

GET_SHOW_RESPONSE = {
    "id": 1433,
    "name": "The Dick Van Dyke Show",
    "first_air_date": "1961-10-03",
    "episode_run_time": [25, 30],
    "overview": "Mid-century sitcom about a comedy writer.",
    "poster_path": "/dvds.jpg",
    "genres": [{"id": 35, "name": "Comedy"}],
}
