"""Synchronous test facade over HTTPX's supported async ASGI transport."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import anyio.to_thread
import httpx
from starlette.types import ASGIApp


class ASGITestClient:
    """Issue synchronous test requests without Starlette's deprecated HTTPX adapter."""

    __test__ = False

    def __init__(
        self,
        app: ASGIApp,
        *,
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
    ) -> None:
        self._app = app
        self._base_url = base_url
        self._raise_server_exceptions = raise_server_exceptions
        self._cookies = httpx.Cookies()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def run_sync_inline(operation: Any, *args: Any, **_options: Any) -> Any:
            return operation(*args)

        async def to_thread_inline(operation: Any, *args: Any, **options: Any) -> Any:
            return operation(*args, **options)

        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self._app,
                raise_app_exceptions=self._raise_server_exceptions,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self._base_url,
                cookies=self._cookies,
                follow_redirects=True,
            ) as client:
                with (
                    patch.object(anyio.to_thread, "run_sync", run_sync_inline),
                    patch.object(asyncio, "to_thread", to_thread_inline),
                ):
                    response = await client.request(method, url, **kwargs)
                self._cookies.update(response.cookies)
                return response

        return asyncio.run(send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)
