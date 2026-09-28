# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Scheduler writer Lease as the Node Agent observes it."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import patch

import kubernetes
import pytest
from node_agent.writer_lease_view import LeaseEpoch, WriterLeaseNotObserved, WriterLeaseView


def _lease(uid: str, transitions: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="nodalarc-scheduler-writer", uid=uid),
        spec=SimpleNamespace(lease_transitions=transitions),
    )


class _Api:
    def __init__(self, listed: list) -> None:
        self.listed = listed
        self.requests: list[dict] = []

    def list_namespaced_lease(self, namespace, **kwargs):
        self.requests.append({"namespace": namespace, **kwargs})
        return SimpleNamespace(
            items=list(self.listed), metadata=SimpleNamespace(resource_version="3")
        )


def _view(listed, events):
    """A started view over one LIST of ``listed`` and a WATCH of ``events``."""
    done = threading.Event()
    hold = threading.Event()

    class _Watch:
        def stream(self, _function, **_kwargs):
            yield from events
            done.set()
            # The WATCH stays open: no new LIST replaces what the events said.
            hold.wait()

        def stop(self) -> None:
            pass

    api = _Api(listed)
    view = WriterLeaseView("nodalarc")
    with patch.object(kubernetes.watch, "Watch", _Watch):
        view.start(api)
        assert view.wait_listed(2)
        assert done.wait(2)
    view.release = hold.set
    return view, api


def test_nothing_is_known_before_the_first_list() -> None:
    view = WriterLeaseView("nodalarc")
    with pytest.raises(WriterLeaseNotObserved):
        view.current()


def test_the_listed_lease_and_each_change_are_observed() -> None:
    view, api = _view(
        [_lease("lease-1", 4)],
        [{"type": "MODIFIED", "object": _lease("lease-1", 5)}],
    )
    try:
        assert view.current() == LeaseEpoch(uid="lease-1", transitions=5)
        assert api.requests[0]["field_selector"] == "metadata.name=nodalarc-scheduler-writer"
    finally:
        view.stop()
        view.release()


def test_an_absent_lease_is_observed_as_none() -> None:
    view, _api = _view([], [])
    try:
        assert view.current() is None
    finally:
        view.stop()
        view.release()


def test_a_deleted_lease_is_observed_as_none() -> None:
    view, _api = _view(
        [_lease("lease-1", 4)], [{"type": "DELETED", "object": _lease("lease-1", 4)}]
    )
    try:
        assert view.current() is None
    finally:
        view.stop()
        view.release()
