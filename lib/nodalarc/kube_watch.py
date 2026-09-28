# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kubernetes objects as the API server reports them, kept current by LIST then WATCH.

One LIST establishes the set, then a WATCH from the list's resourceVersion
reports every later change. The whole current set is delivered after the
LIST and after each change. A new LIST follows a WATCH that:

- the API server ended at its timeout,
- fell behind the API server's history (HTTP 410), or
- stayed silent past its own timeout. The API server ends every WATCH at its
  timeout, so a longer silence means a connection lost without notice, or
- lost its connection.

Every request carries a client-side timeout, so a lost connection never
blocks a reader for good. Any other API refusal raises.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0
# A LIST answers at once; one that does not within this time is lost.
LIST_READ_TIMEOUT_S = 30.0
# How long a WATCH read may stay silent past the WATCH's own timeout before
# the connection counts as lost.
WATCH_READ_GRACE_S = 15.0

_END = object()


def object_name(obj: Any) -> str:
    if isinstance(obj, Mapping):
        return str((obj.get("metadata") or {}).get("name") or "")
    return str(obj.metadata.name or "")


def object_uid(obj: Any) -> str:
    if isinstance(obj, Mapping):
        return str((obj.get("metadata") or {}).get("uid") or "")
    return str(obj.metadata.uid or "")


def _listing_parts(listing: Any) -> tuple[list[Any], str]:
    if isinstance(listing, Mapping):
        items = list(listing.get("items") or [])
        return items, str((listing.get("metadata") or {}).get("resourceVersion") or "")
    return list(listing.items or []), str(listing.metadata.resource_version or "")


def _recording_watch_requests(
    list_fn: Callable[..., Any], opened: Callable[[Any], None]
) -> Callable[..., Any]:
    """``list_fn``, reporting each WATCH response it opens to ``opened``."""

    @functools.wraps(list_fn)
    def _list(*args: Any, **kwargs: Any) -> Any:
        response = list_fn(*args, **kwargs)
        if kwargs.get("watch"):
            opened(response)
        return response

    return _list


def list_then_watch(
    list_fn: Callable[..., Any],
    *,
    request_seconds: Callable[[], float],
    stopped: Callable[[], bool],
    key: Callable[[Any], str] = object_name,
    opened: Callable[[Any], None] | None = None,
    **list_kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """Yield {key: object} for every object ``list_fn`` lists, now and after each change.

    Runs until ``stopped()`` is true, after at least one LIST. ``request_seconds()`` bounds each WATCH
    request. ``opened`` receives each WATCH response as it opens, so another
    thread can end a blocked read.
    """
    import kubernetes.client
    import kubernetes.watch
    import urllib3

    watched = list_fn if opened is None else _recording_watch_requests(list_fn, opened)
    # The first LIST always runs: a caller that stops at once still observed the set.
    while True:
        listing = list_fn(**list_kwargs, _request_timeout=(CONNECT_TIMEOUT_S, LIST_READ_TIMEOUT_S))
        items, resource_version = _listing_parts(listing)
        objects = {key(obj): obj for obj in items}
        yield dict(objects)
        if stopped():
            return
        seconds = max(1, int(request_seconds()))
        watch = kubernetes.watch.Watch()
        try:
            for event in watch.stream(
                watched,
                resource_version=resource_version,
                timeout_seconds=seconds,
                _request_timeout=(CONNECT_TIMEOUT_S, seconds + WATCH_READ_GRACE_S),
                **list_kwargs,
            ):
                if stopped():
                    return
                obj = event["object"]
                if event["type"] == "DELETED":
                    objects.pop(key(obj), None)
                elif event["type"] in ("ADDED", "MODIFIED"):
                    objects[key(obj)] = obj
                else:
                    continue
                yield dict(objects)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status != 410:
                raise
            # The WATCH fell behind the API server's history: list again.
        except urllib3.exceptions.ReadTimeoutError:
            log.warning(
                "WATCH silent %.0f s past its %d s timeout; the connection is lost, listing again",
                WATCH_READ_GRACE_S,
                seconds,
            )
        except urllib3.exceptions.ProtocolError as exc:
            log.warning("WATCH connection broke (%s); listing again", exc)
        finally:
            watch.stop()
        if stopped():
            return


async def watch_snapshots(
    list_fn: Callable[..., Any],
    *,
    timeout_s: float,
    **list_kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Yield {name: object} for every object ``list_fn`` lists, now and after each change.

    The LIST and WATCH run in a worker thread. Raises TimeoutError once
    ``timeout_s`` passes without the consumer stopping, and re-raises any API
    failure ``list_then_watch`` raises. A consumer that stops ends the worker's
    blocked read at once.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    deadline = time.monotonic() + timeout_s
    open_response: dict[str, Any] = {}

    def _opened(response: Any) -> None:
        open_response["response"] = response
        # A consumer that stopped while this request was being made found no
        # response to end; end it here.
        if stop.is_set():
            _end_read(response)

    def _emit(item: Any) -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(queue.put_nowait, item)

    def _run() -> None:
        try:
            for snapshot in list_then_watch(
                list_fn,
                request_seconds=lambda: deadline - time.monotonic(),
                stopped=lambda: stop.is_set() or time.monotonic() >= deadline,
                opened=_opened,
                **list_kwargs,
            ):
                _emit(snapshot)
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
