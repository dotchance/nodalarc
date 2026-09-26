# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kubernetes objects as the API server reports them, delivered as they change.

One LIST establishes the set, then a WATCH from the list's resourceVersion
reports every later change. A consumer receives the whole current set after
the LIST and after each event, and stops iterating once the set satisfies
it. A watch the API server ends is followed by a new LIST.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)

_END = object()


def _object_name(obj: Any) -> str:
    if isinstance(obj, Mapping):
        return str((obj.get("metadata") or {}).get("name") or "")
    return str(obj.metadata.name or "")


def _listing_parts(listing: Any) -> tuple[list[Any], str]:
    if isinstance(listing, Mapping):
        items = list(listing.get("items") or [])
        return items, str((listing.get("metadata") or {}).get("resourceVersion") or "")
    return list(listing.items or []), str(listing.metadata.resource_version or "")


async def watch_snapshots(
    list_fn: Callable[..., Any],
    *,
    timeout_s: float,
    **list_kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Yield {name: object} for every object ``list_fn`` lists, now and after each change.

    Raises TimeoutError once ``timeout_s`` passes without the consumer
    stopping, and re-raises any API failure other than an expired
    resourceVersion (which starts a new LIST).
    """
    import kubernetes.client
    import kubernetes.watch

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    deadline = time.monotonic() + timeout_s
    # The open watch response, so a consumer that has what it needs can end
    # the worker thread's blocked read at once.
    open_response: dict[str, Any] = {}

    @functools.wraps(list_fn)
    def _recording_list(*args: Any, **kwargs: Any) -> Any:
        response = list_fn(*args, **kwargs)
        if kwargs.get("watch"):
            open_response["response"] = response
            # A consumer that stopped while this request was being made
            # found no response to end; end it here.
            if stop.is_set():
                _end_read(response)
        return response

    def _emit(item: Any) -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(queue.put_nowait, item)

    def _run() -> None:
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                items, resource_version = _listing_parts(list_fn(**list_kwargs))
                objects = {_object_name(obj): obj for obj in items}
                _emit(dict(objects))
                watch = kubernetes.watch.Watch()
                try:
                    for event in watch.stream(
                        _recording_list,
                        resource_version=resource_version,
                        timeout_seconds=max(1, int(deadline - time.monotonic())),
                        **list_kwargs,
                    ):
                        if stop.is_set():
                            return
                        obj = event["object"]
                        if event["type"] == "DELETED":
                            objects.pop(_object_name(obj), None)
                        elif event["type"] in ("ADDED", "MODIFIED"):
                            objects[_object_name(obj)] = obj
                        _emit(dict(objects))
                except kubernetes.client.rest.ApiException as exc:
                    if exc.status != 410:
                        raise
                    # The watch fell behind the API server's history: list again.
                finally:
                    watch.stop()
        except BaseException as exc:
            if not stop.is_set():
                _emit(exc)
        finally:
            _emit(_END)

    thread = threading.Thread(target=_run, name="kube-watch", daemon=True)
    thread.start()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no satisfying state within {timeout_s:.0f} s")
            try:
                item = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                raise TimeoutError(f"no satisfying state within {timeout_s:.0f} s") from None
            if item is _END:
                raise TimeoutError(f"no satisfying state within {timeout_s:.0f} s")
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        response = open_response.get("response")
        if response is not None:
            _end_read(response)


def _end_read(response: Any) -> None:
    """End a blocked read of ``response`` from another thread, without waiting.

    Shutting the socket's read side returns the reader at once; the reader's
    own cleanup then closes the response. ``close()`` here would wait for the
    buffer lock the blocked reader holds, until the API server ended the
    watch. A response whose connection is already released (RuntimeError),
    that has no socket (ValueError), or whose socket is already closed
    (OSError) has no blocked reader to end.
    """
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        response.shutdown()
