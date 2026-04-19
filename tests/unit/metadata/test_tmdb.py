"""TmdbClient model parsing + cache behavior (no real network)."""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from archive_agent.metadata import TmdbClient, TmdbError
from archive_agent.metadata.models import TmdbConfiguration, TmdbMovie, TmdbShow

from .conftest import (
    CONFIGURATION_RESPONSE,
    GENRES_MOVIE_RESPONSE,
    GET_MOVIE_RESPONSE,
    GET_SHOW_RESPONSE,
    SEARCH_MOVIE_RESPONSE,
    SEARCH_TV_RESPONSE,
)

# --- model parsing ---


def test_tmdb_movie_parses_search_shape() -> None:
    m = TmdbMovie.model_validate(SEARCH_MOVIE_RESPONSE["results"][0])
    assert m.id == 22660
    assert m.year == 2008
    assert m.genre_ids == [16, 10402]
    assert m.genres == []  # not in search shape
    # runtime is absent in search → None
    assert m.runtime is None


def test_tmdb_movie_parses_by_id_shape() -> None:
    m = TmdbMovie.model_validate(GET_MOVIE_RESPONSE)
    assert m.runtime == 82
    assert [g.name for g in m.genres] == ["Animation", "Music"]
    assert m.genre_ids == []  # not in by-id shape


def test_tmdb_show_parses_by_id_shape() -> None:
    s = TmdbShow.model_validate(GET_SHOW_RESPONSE)
    assert s.name == "The Dick Van Dyke Show"
    assert s.year == 1961
    assert s.episode_run_time == [25, 30]


def test_tmdb_configuration_from_api() -> None:
    cfg = TmdbConfiguration.from_api(CONFIGURATION_RESPONSE)
    assert cfg.images_base_url == "https://image.tmdb.org/t/p/"
    assert "w342" in cfg.poster_sizes


def test_year_property_handles_empty_date() -> None:
    assert TmdbMovie(id=1, title="x", release_date=None).year is None
    assert TmdbMovie(id=1, title="x", release_date="").year is None
    assert TmdbMovie(id=1, title="x", release_date="not-a-date").year is None


# --- HTTP + cache plumbing (mocked transport) ---


class _MockTransport(httpx.AsyncBaseTransport):
    """Replay a fixed response map keyed by path — ignores query params."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.call_log: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.call_log.append(path)
        if path in self.responses:
            value = self.responses[path]
            if callable(value):
                return value(request)
            return httpx.Response(200, json=value)
        return httpx.Response(404, json={"status_code": 34, "status_message": "not found"})


@pytest.fixture
def tmdb_client_factory(db: sqlite3.Connection):  # type: ignore[no-untyped-def]
    async def _make(transport: _MockTransport) -> TmdbClient:
        client = TmdbClient(SecretStr("fake-key"), db)
        await client.__aenter__()
        # Replace the httpx.AsyncClient with one using our mock transport
        await client._client.aclose()  # type: ignore[union-attr]
        client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="https://api.themoviedb.org/3",
            transport=transport,
        )
        return client

    return _make


async def test_search_movie_hits_network_then_cache(tmdb_client_factory, db) -> None:  # type: ignore[no-untyped-def]
    transport = _MockTransport({"/3/search/movie": SEARCH_MOVIE_RESPONSE})
    client = await tmdb_client_factory(transport)
    result = await client.search_movie("Sita Sings the Blues", 2008)
    assert result is not None
    assert result.id == 22660
    assert len(transport.call_log) == 1

    # Second call — cache hit, no new HTTP
    result2 = await client.search_movie("Sita Sings the Blues", 2008)
    assert result2 is not None
    assert result2.id == 22660
    assert len(transport.call_log) == 1  # still just one call

    await client.__aexit__(None, None, None)


async def test_search_movie_returns_none_on_empty_results(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    transport = _MockTransport({"/3/search/movie": {"page": 1, "total_results": 0, "results": []}})
    client = await tmdb_client_factory(transport)
    assert await client.search_movie("does not exist", None) is None
    await client.__aexit__(None, None, None)


async def test_429_is_retried_then_succeeds(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=SEARCH_MOVIE_RESPONSE)

    transport = _MockTransport({"/3/search/movie": _handler})
    client = await tmdb_client_factory(transport)
    result = await client.search_movie("Sita", 2008)
    assert result is not None
    assert calls["n"] == 2  # one 429, one success
    await client.__aexit__(None, None, None)


async def test_persistent_500_raises_tmdb_error(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    transport = _MockTransport({"/3/search/movie": lambda req: httpx.Response(503)})
    client = await tmdb_client_factory(transport)
    with pytest.raises(TmdbError):
        await client.search_movie("Sita", 2008)
    await client.__aexit__(None, None, None)


async def test_client_cannot_be_used_outside_context(db: sqlite3.Connection) -> None:
    client = TmdbClient(SecretStr("k"), db)
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.configuration()


async def test_build_poster_url_uses_config(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    transport = _MockTransport({"/3/configuration": CONFIGURATION_RESPONSE})
    client = await tmdb_client_factory(transport)
    url = await client.build_poster_url("/abc.jpg")
    assert url == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert await client.build_poster_url(None) is None
    await client.__aexit__(None, None, None)


async def test_genre_names_resolves_ids(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    transport = _MockTransport({"/3/genre/movie/list": GENRES_MOVIE_RESPONSE})
    client = await tmdb_client_factory(transport)
    names = await client.genre_names("movie", [16, 35])
    assert names == ["Animation", "Comedy"]
    # Unknown id silently dropped
    assert await client.genre_names("movie", [16, 99999]) == ["Animation"]
    await client.__aexit__(None, None, None)


async def test_search_show_uses_first_air_date_year(tmdb_client_factory) -> None:  # type: ignore[no-untyped-def]
    captured = {"params": None}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=SEARCH_TV_RESPONSE)

    transport = _MockTransport({"/3/search/tv": _handler})
    client = await tmdb_client_factory(transport)
    result = await client.search_show("The Dick Van Dyke Show", 1961)
    assert result is not None
    assert result.id == 1433
    assert captured["params"]["first_air_date_year"] == "1961"
    assert captured["params"]["query"] == "The Dick Van Dyke Show"
    await client.__aexit__(None, None, None)
