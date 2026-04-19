"""Async TMDb v3 client with SQLite-backed caching and 429 handling.

Only this module talks HTTP to TMDb; higher-level enrichment goes
through ``metadata.enrich``. The API key rides in the query string —
TMDb's accepted form — and the structlog redactor catches it because
it lives in ``params`` not a logged dict (see phase1-06's redactor;
``api_key`` matches the REDACT_FIELDS rule).

Retries: exponential back-off (100 ms → 400 ms → 1.6 s), respects
``Retry-After`` on 429s, 4 simultaneous in-flight requests max.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from types import TracebackType
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from archive_agent.logging import get_logger
from archive_agent.metadata import cache as metadata_cache
from archive_agent.metadata.models import TmdbConfiguration, TmdbMovie, TmdbShow

__all__ = ["TmdbClient", "TmdbError"]

log = get_logger("archive_agent.metadata.tmdb")

_BASE_URL = "https://api.themoviedb.org/3"
_TTL_SEARCH = timedelta(days=14)
_TTL_BY_ID = timedelta(days=30)
_TTL_CONFIGURATION = timedelta(hours=24)
_TTL_GENRES = timedelta(days=30)
_DEFAULT_POSTER_SIZE = "w342"
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.4, 1.6)


class TmdbError(Exception):
    """Raised when TMDb returns a non-retryable error or exhausts retries."""


class TmdbClient:
    def __init__(
        self,
        api_key: SecretStr,
        conn: sqlite3.Connection,
        *,
        timeout: httpx.Timeout | None = None,
        concurrency: int = 4,
    ) -> None:
        self._api_key = api_key
        self._conn = conn
        self._timeout = timeout or httpx.Timeout(15.0)
        self._concurrency = concurrency
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    async def __aenter__(self) -> TmdbClient:
        self._client = httpx.AsyncClient(base_url=_BASE_URL, timeout=self._timeout)
        self._semaphore = asyncio.Semaphore(self._concurrency)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TmdbClient must be used as an async context manager")
        return self._client

    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            raise RuntimeError("TmdbClient must be used as an async context manager")
        return self._semaphore

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """HTTP GET with auth + retry. Raises TmdbError on persistent failure."""
        full_params: dict[str, Any] = dict(params or {})
        full_params["api_key"] = self._api_key.get_secret_value()

        async with self._sem():
            last_status: int | None = None
            last_exc: Exception | None = None
            for delay in _RETRY_DELAYS:
                try:
                    r = await self._http().get(path, params=full_params)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    log.warning("tmdb_network_error", path=path, error=str(exc))
                    await asyncio.sleep(delay)
                    continue
                last_status = r.status_code
                if r.status_code == 429:
                    wait_for = float(r.headers.get("Retry-After") or delay)
                    log.info("tmdb_rate_limited", path=path, wait_s=wait_for)
                    await asyncio.sleep(wait_for)
                    continue
                if r.status_code >= 500:
                    log.warning("tmdb_server_error", path=path, status=r.status_code)
                    await asyncio.sleep(delay)
                    continue
                if r.status_code >= 400:
                    raise TmdbError(f"TMDb {r.status_code} on {path}: {r.text[:200]}")
                data: dict[str, Any] = r.json()
                return data

            raise TmdbError(
                f"TMDb {path}: retries exhausted (last_status={last_status}, last_exc={last_exc})"
            )

    # --- public surface ---------------------------------------------------

    async def configuration(self) -> TmdbConfiguration:
        """Cached 24h. Needed to build poster URLs."""
        cached = metadata_cache.get(self._conn, "configuration")
        if cached is None:
            cached = await self._get("/configuration")
            metadata_cache.put(self._conn, "configuration", cached, _TTL_CONFIGURATION)
        return TmdbConfiguration.from_api(cached)

    async def _genre_map(self, kind: Literal["movie", "tv"]) -> dict[int, str]:
        key = f"genres:{kind}"
        cached = metadata_cache.get(self._conn, key)
        if cached is None:
            cached = await self._get(f"/genre/{kind}/list")
            metadata_cache.put(self._conn, key, cached, _TTL_GENRES)
        return {int(g["id"]): str(g["name"]) for g in cached.get("genres", [])}

    async def search_movie(self, title: str, year: int | None) -> TmdbMovie | None:
        """Highest-ranked result, or None if TMDb returns nothing.

        Includes ``primary_release_year`` when provided — disambiguates
        films-sharing-a-title across decades (e.g., The Lost World 1925 vs 1960).
        """
        key = f"search:movie:{title.strip().lower()}:{year or ''}"
        cached = metadata_cache.get(self._conn, key)
        if cached is None:
            params: dict[str, Any] = {"query": title}
            if year is not None:
                params["primary_release_year"] = year
            cached = await self._get("/search/movie", params=params)
            metadata_cache.put(self._conn, key, cached, _TTL_SEARCH)
        results = cached.get("results") or []
        if not results:
            return None
        return TmdbMovie.model_validate(results[0])

    async def search_show(self, title: str, year: int | None) -> TmdbShow | None:
        key = f"search:tv:{title.strip().lower()}:{year or ''}"
        cached = metadata_cache.get(self._conn, key)
        if cached is None:
            params: dict[str, Any] = {"query": title}
            if year is not None:
                params["first_air_date_year"] = year
            cached = await self._get("/search/tv", params=params)
            metadata_cache.put(self._conn, key, cached, _TTL_SEARCH)
        results = cached.get("results") or []
        if not results:
            return None
        return TmdbShow.model_validate(results[0])

    async def get_movie(self, tmdb_id: int) -> TmdbMovie:
        key = f"id:movie:{tmdb_id}"
        cached = metadata_cache.get(self._conn, key)
        if cached is None:
            cached = await self._get(f"/movie/{tmdb_id}")
            metadata_cache.put(self._conn, key, cached, _TTL_BY_ID)
        return TmdbMovie.model_validate(cached)

    async def get_show(self, tmdb_id: int) -> TmdbShow:
        key = f"id:tv:{tmdb_id}"
        cached = metadata_cache.get(self._conn, key)
        if cached is None:
            cached = await self._get(f"/tv/{tmdb_id}")
            metadata_cache.put(self._conn, key, cached, _TTL_BY_ID)
        return TmdbShow.model_validate(cached)

    async def build_poster_url(
        self, poster_path: str | None, size: str = _DEFAULT_POSTER_SIZE
    ) -> str | None:
        """Compose the full URL from the configuration's base + size + path."""
        if not poster_path:
            return None
        config = await self.configuration()
        if not config.images_base_url:
            return None
        return f"{config.images_base_url}{size}{poster_path}"

    async def genre_names(self, kind: Literal["movie", "tv"], genre_ids: list[int]) -> list[str]:
        """Resolve a list of genre ids (from search responses) to names."""
        if not genre_ids:
            return []
        mapping = await self._genre_map(kind)
        return [mapping[g] for g in genre_ids if g in mapping]
