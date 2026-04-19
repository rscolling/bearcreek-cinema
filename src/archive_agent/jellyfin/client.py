"""Async Jellyfin REST client.

Only this module speaks HTTP to Jellyfin. Other code calls these methods
or the helpers in ``jellyfin.history``. Auth is via the ``X-Emby-Token``
header; the API key is carried as ``SecretStr`` so accidental logging
redacts automatically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

import httpx
from pydantic import SecretStr

from archive_agent.jellyfin.models import (
    JellyfinItem,
    JellyfinItemPage,
    JellyfinLibrary,
    JellyfinServerInfo,
    JellyfinUser,
    JellyfinUserData,
)

__all__ = ["DEFAULT_PAGE_SIZE", "DEFAULT_TIMEOUT", "JellyfinClient"]

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DEFAULT_PAGE_SIZE = 200


class JellyfinClient:
    """Async client for the Jellyfin REST API.

    Use as an async context manager — the underlying ``httpx.AsyncClient``
    is created on ``__aenter__`` and closed on ``__aexit__`` so the
    connection pool is cleaned up. Outside a context, method calls raise
    ``RuntimeError``.
    """

    def __init__(
        self,
        url: str,
        api_key: SecretStr,
        user_id: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._user_id = user_id
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> JellyfinClient:
        self._client = httpx.AsyncClient(
            base_url=self._url,
            timeout=self._timeout,
            headers={
                "X-Emby-Token": self._api_key.get_secret_value(),
                "Accept": "application/json",
            },
        )
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
            raise RuntimeError("JellyfinClient must be used as an async context manager")
        return self._client

    @property
    def user_id(self) -> str:
        return self._user_id

    # --- Server-level ----------------------------------------------------

    async def ping(self) -> JellyfinServerInfo:
        """GET ``/System/Info/Public`` — confirms reachability and
        returns basic server identity. Does not require auth."""
        r = await self._http().get("/System/Info/Public")
        r.raise_for_status()
        return JellyfinServerInfo.model_validate(r.json())

    async def authenticate(self) -> None:
        """GET ``/System/Info`` — verifies the API key works."""
        r = await self._http().get("/System/Info")
        r.raise_for_status()

    # --- Users + libraries ----------------------------------------------

    async def get_user(self, user_id: str | None = None) -> JellyfinUser:
        uid = user_id or self._user_id
        r = await self._http().get(f"/Users/{uid}")
        r.raise_for_status()
        return JellyfinUser.model_validate(r.json())

    async def list_users(self) -> list[JellyfinUser]:
        r = await self._http().get("/Users")
        r.raise_for_status()
        return [JellyfinUser.model_validate(u) for u in r.json()]

    async def list_libraries(self) -> list[JellyfinLibrary]:
        """Return the libraries visible to the configured user."""
        r = await self._http().get(f"/Users/{self._user_id}/Views")
        r.raise_for_status()
        return [JellyfinLibrary.model_validate(i) for i in r.json()["Items"]]

    # --- Items ----------------------------------------------------------

    async def list_items(
        self,
        *,
        library_id: str | None = None,
        include_item_types: list[str] | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        start_index: int = 0,
        filters: list[str] | None = None,
    ) -> JellyfinItemPage:
        params: dict[str, Any] = {
            "Recursive": "true",
            "StartIndex": start_index,
        }
        if library_id is not None:
            params["ParentId"] = library_id
        if include_item_types:
            params["IncludeItemTypes"] = ",".join(include_item_types)
        if fields:
            params["Fields"] = ",".join(fields)
        if filters:
            params["Filters"] = ",".join(filters)
        if limit is not None:
            params["Limit"] = limit
        r = await self._http().get(f"/Users/{self._user_id}/Items", params=params)
        r.raise_for_status()
        return JellyfinItemPage.model_validate(r.json())

    async def list_items_paginated(
        self,
        *,
        library_id: str | None = None,
        include_item_types: list[str] | None = None,
        fields: list[str] | None = None,
        filters: list[str] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[JellyfinItem]:
        """Stream through pagination, yielding one item at a time."""
        start = 0
        while True:
            page = await self.list_items(
                library_id=library_id,
                include_item_types=include_item_types,
                fields=fields,
                filters=filters,
                limit=page_size,
                start_index=start,
            )
            if not page.items:
                return
            for item in page.items:
                yield item
            start += len(page.items)
            if start >= page.total_record_count:
                return

    async def get_item(self, item_id: str) -> JellyfinItem:
        r = await self._http().get(f"/Users/{self._user_id}/Items/{item_id}")
        r.raise_for_status()
        return JellyfinItem.model_validate(r.json())

    async def get_user_data(self, item_id: str) -> JellyfinUserData:
        """Fetch UserData for a single item by re-reading the item with
        UserData embedded — Jellyfin doesn't expose a standalone endpoint."""
        item = await self.get_item(item_id)
        return item.user_data or JellyfinUserData()

    # --- Library scan ---------------------------------------------------

    async def trigger_library_scan(self, library_id: str | None = None) -> None:
        """POST ``/Library/Refresh`` (whole server) or
        ``/Items/{id}/Refresh`` (one library)."""
        if library_id is not None:
            r = await self._http().post(f"/Items/{library_id}/Refresh")
        else:
            r = await self._http().post("/Library/Refresh")
        r.raise_for_status()

    # --- Escape hatch ---------------------------------------------------

    async def raw_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch any endpoint not yet wrapped. Returns parsed JSON dict."""
        r = await self._http().get(path, params=params)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data
