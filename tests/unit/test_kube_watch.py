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
from nodalarc.kube_watch import list_then_watch, watch_snapshots


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


# ---------------------------------------------------------------------------
# list_then_watch: every request bounded, a lost WATCH connection lists again
# ---------------------------------------------------------------------------


class _CountingApi:
    """A LIST that reports how often it ran and what each request carried."""

    def __init__(self) -> None:
        self.lists: list[dict] = []

    def list_namespaced_pod(self, namespace, **kwargs):
        self.lists.append(kwargs)
        return SimpleNamespace(
            items=[_pod(f"p{len(self.lists)}")],
            metadata=SimpleNamespace(resource_version=str(len(self.lists))),
        )


def _failing_watches(*failures):
    """A Watch whose successive streams raise ``failures`` in order, recording each request."""
    requests: list[dict] = []
    remaining = list(failures)

    class _Watch:
        def stream(self, _function, **kwargs):
            requests.append(kwargs)
            if remaining:
                raise remaining.pop(0)
            yield from ()

        def stop(self) -> None:
            pass

    return _Watch, requests


def _run_list_then_watch(api, watch_class, *, snapshots: int) -> list[list[str]]:
    seen: list[list[str]] = []
    with patch.object(kubernetes.watch, "Watch", watch_class):
        for pods in list_then_watch(
            api.list_namespaced_pod,
            request_seconds=lambda: 120,
            stopped=lambda: len(seen) >= snapshots,
            namespace="nodalarc",
        ):
            seen.append(sorted(pods))
    return seen


def test_every_list_and_watch_request_carries_a_client_timeout() -> None:
    api = _CountingApi()
    watch_class, requests = _failing_watches()
    _run_list_then_watch(api, watch_class, snapshots=2)
    assert api.lists[0]["_request_timeout"] == (10.0, 30.0)
    assert requests[0]["timeout_seconds"] == 120
    # A read silent past the WATCH's own timeout ends in the client.
    assert requests[0]["_request_timeout"] == (10.0, 135.0)


@pytest.mark.parametrize(
    "lost",
    [
        pytest.param(
            lambda: __import__("urllib3").exceptions.ReadTimeoutError(
                None, None, "Read timed out."
            ),
            id="silent",
        ),
        pytest.param(
            lambda: __import__("urllib3").exceptions.ProtocolError("Connection broken"),
            id="broken",
        ),
        pytest.param(
            lambda: kubernetes.client.rest.ApiException(status=410, reason="Gone"),
            id="expired",
        ),
    ],
)
def test_a_lost_or_expired_watch_lists_again(lost) -> None:
    api = _CountingApi()
    watch_class, _requests = _failing_watches(lost())
    seen = _run_list_then_watch(api, watch_class, snapshots=2)
    assert seen == [["p1"], ["p2"]]
    assert len(api.lists) == 2


def test_any_other_api_refusal_raises() -> None:
    api = _CountingApi()
    watch_class, _requests = _failing_watches(
        kubernetes.client.rest.ApiException(status=403, reason="Forbidden")
    )
    with pytest.raises(kubernetes.client.rest.ApiException) as refused:
        _run_list_then_watch(api, watch_class, snapshots=5)
    assert refused.value.status == 403
