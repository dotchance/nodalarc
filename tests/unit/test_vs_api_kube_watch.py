# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kubernetes state delivered as it changes: one LIST, then a WATCH."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

import kubernetes
import pytest
from vs_api.kube_watch import watch_snapshots


def _pod(name: str) -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(name=name))


class _Response:
    """A watch response whose reader blocks until the socket's read side is shut.

    ``close()`` from another thread would wait for the blocked reader, as the
    buffered socket reader's lock makes it do; the consumer must never call it.
    """

    def __init__(self) -> None:
        self.read_ended = threading.Event()
        self.closed_by_consumer = False

    def shutdown(self) -> None:
        self.read_ended.set()

    def close(self) -> None:
        self.closed_by_consumer = True


class _Api:
    def __init__(self, listed: list[str]) -> None:
        self.listed = listed
        self.response = _Response()

    def list_namespaced_pod(self, namespace, **kwargs):
        if kwargs.get("watch"):
            return self.response
        return SimpleNamespace(
            items=[_pod(name) for name in self.listed],
            metadata=SimpleNamespace(resource_version="7"),
        )


def _watch_class(events, *, block: threading.Event):
    class _Watch:
        def stream(self, function, **kwargs):
            assert kwargs["resource_version"] == "7"
            response = function(watch=True, **kwargs)  # opens the response, as the client does
            yield from events
            # The reader blocks until the socket's read side is shut (or the test ends).
            while not (response.read_ended.is_set() or block.is_set()):
                response.read_ended.wait(0.05)

        def stop(self) -> None:
            pass

    return _Watch


def _collect(api, events, *, until, timeout_s=5.0):
    block = threading.Event()

    async def _run():
        seen = []
        async for pods in watch_snapshots(
            api.list_namespaced_pod, timeout_s=timeout_s, namespace="nodalarc"
        ):
            seen.append(sorted(pods))
            if until(pods):
                break
        return seen

    try:
        with patch.object(kubernetes.watch, "Watch", _watch_class(events, block=block)):
            return asyncio.run(_run())
    finally:
        block.set()


def test_the_listed_state_comes_first_and_events_update_it() -> None:
    api = _Api(["a", "b"])
    seen = _collect(
        api,
        [
            {"type": "DELETED", "object": _pod("a")},
            {"type": "ADDED", "object": _pod("c")},
            {"type": "DELETED", "object": _pod("b")},
            {"type": "DELETED", "object": _pod("c")},
        ],
        until=lambda pods: not pods,
    )
    assert seen == [["a", "b"], ["b"], ["b", "c"], ["c"], []]


def test_a_satisfied_consumer_ends_the_blocked_read_without_waiting() -> None:
    import time

    api = _Api(["a"])
    started = time.monotonic()
    _collect(api, [{"type": "DELETED", "object": _pod("a")}], until=lambda pods: not pods)
    assert time.monotonic() - started < 1.0
    assert api.response.read_ended.wait(2)
    assert not api.response.closed_by_consumer


def test_a_consumer_satisfied_before_the_watch_opens_still_ends_its_read() -> None:
    """The listed state alone satisfies the consumer: the watch request made
    after it is ended as soon as it returns."""
    api = _Api([])
    _collect(api, [], until=lambda pods: not pods)
    assert api.response.read_ended.wait(2)


def test_a_consumer_never_satisfied_times_out() -> None:
    api = _Api(["a"])
    with pytest.raises(TimeoutError):
        _collect(api, [], until=lambda pods: False, timeout_s=1.0)
