"""Shared async/sync bridge and managed HTTP client lifecycle utilities."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine, Optional, TypeVar

import httpx

T = TypeVar("T")


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Execute a coroutine synchronously, safely handling an already-running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        def _worker_runner() -> T:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            try:
                return worker_loop.run_until_complete(coro)
            finally:
                try:
                    worker_loop.run_until_complete(worker_loop.shutdown_asyncgens())
                finally:
                    worker_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_worker_runner).result()

    return asyncio.run(coro)


class HttpClientMixin:
    """Mixin managing an optional external or loop-bound persistent httpx.AsyncClient."""

    _external_client: Optional[httpx.AsyncClient] = None
    _internal_client: Optional[httpx.AsyncClient] = None
    _bound_loop: Optional[asyncio.AbstractEventLoop] = None
    _http_timeout: Optional[float] = None

    def _init_http_client(
        self,
        client: Optional[httpx.AsyncClient] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Initialize HTTP client state with optional external client and timeout."""
        self._external_client = client
        self._internal_client = None
        self._bound_loop = None
        self._http_timeout = timeout

    def _resolve_http_timeout(self) -> float:
        if self._http_timeout is not None:
            return self._http_timeout
        settings = getattr(self, "settings", None)
        if settings is not None:
            return float(getattr(settings, "default_timeout_seconds", 60.0))
        return 60.0

    def _get_client(self) -> httpx.AsyncClient:
        """Return the active HTTP client: external injection or internal persistent pool."""
        if self._external_client is not None:
            return self._external_client
        try:
            current_loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            self._internal_client is None
            or self._internal_client.is_closed
            or self._bound_loop is not current_loop
        ):
            self._internal_client = httpx.AsyncClient(timeout=self._resolve_http_timeout())
            self._bound_loop = current_loop
        return self._internal_client

    async def aclose(self) -> None:
        """Close persistent internal HTTP connection pool if owned."""
        if self._internal_client is not None and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None
