# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The wiring manifest watch: one LIST, then a WATCH, every change observed."""

from __future__ import annotations

import threading
from unittest.mock import patch

import kubernetes
from kubernetes.client import V1ConfigMap, V1ConfigMapList, V1ListMeta, V1ObjectMeta
from node_agent.manifest_watch import ManifestWatch


def _cm(resource_version: str, uid: str = "cm-1") -> V1ConfigMap:
    return V1ConfigMap(
        metadata=V1ObjectMeta(
            name="nodalarc-topology-wiring", resource_version=resource_version, uid=uid
        )
    )


class _Api:
    """A ConfigMap API: the LIST answers once, then the watch streams events."""

    def __init__(self, listed, events, *, then_block: threading.Event) -> None:
        self.listed = listed
        self.events = events
        self.then_block = then_block
        self.lists: list[dict] = []

    def list_namespaced_config_map(self, namespace, **kwargs):
        self.lists.append(kwargs)
        return V1ConfigMapList(items=list(self.listed), metadata=V1ListMeta(resource_version="10"))


class _Watch:
    api: _Api

    def stream(self, _function, **kwargs):
        assert kwargs["namespace"] == "nodalarc"
        assert kwargs["resource_version"] == "10"
        assert kwargs["field_selector"] == "metadata.name=nodalarc-topology-wiring"
        yield from self.api.events
        self.api.then_block.wait()

    def stop(self) -> None:
        pass


def _run(listed, events):
    block = threading.Event()
    api = _Api(listed, events, then_block=block)
    _Watch.api = api
    with patch.object(kubernetes.watch, "Watch", _Watch):
        watch = ManifestWatch(api, "nodalarc", "nodalarc-topology-wiring")
        watch.start()
        return watch, api, block


def test_the_listed_manifest_is_observed_first() -> None:
    watch, api, block = _run([_cm("7")], [])
    try:
        state = watch.current(timeout=2)
        assert state is not None and state.present
        assert state.resource_version == "7"
        assert api.lists[0] == {
            "field_selector": "metadata.name=nodalarc-topology-wiring",
            # A LIST that never answers ends in the client.
            "_request_timeout": (10.0, 30.0),
        }
    finally:
        watch.stop()
        block.set()


def test_a_new_manifest_and_its_removal_wake_the_waiter() -> None:
    watch, _api, block = _run(
        [_cm("7")],
        [
            {"type": "MODIFIED", "object": _cm("8")},
            {"type": "DELETED", "object": _cm("8")},
        ],
    )
    try:
        # The LIST (version 1), the new manifest (2) and its removal (3).
        state = watch.wait_for_change(2, timeout=2)
        assert state is not None and state.version == 3
        assert not state.present
        assert watch.changed_since(1)
        assert not watch.changed_since(3)
    finally:
        watch.stop()
        block.set()


def test_absence_is_an_observation() -> None:
    watch, _api, block = _run([], [])
    try:
        state = watch.current(timeout=2)
        assert state is not None and not state.present
        assert state.resource_version == ""
    finally:
        watch.stop()
        block.set()
