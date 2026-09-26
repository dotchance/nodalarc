# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The wiring manifest as the API server last reported it, kept current by a watch.

The Node Agent's wiring watcher reads the manifest from here instead of
polling it. One LIST establishes the state, then a WATCH from that list's
resourceVersion delivers every later change the moment the API server
commits it. A watch that ends (server timeout, dropped connection, expired
resourceVersion) is followed by a new LIST; there is no other source of
manifest state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# How long one WATCH request stays open before the API server ends it and a
# new LIST starts. The state stays current throughout; this only bounds how
# long one request lives.
_WATCH_REQUEST_SECONDS = 300
# Pause before listing again after the API server refused a request.
_RELIST_AFTER_ERROR_S = 1.0


@dataclass(frozen=True, slots=True)
class ManifestState:
    """One observation of the wiring manifest ConfigMap."""

    # Increments on every observed change, absence included.
    version: int
    # None when the ConfigMap does not exist.
    config_map: Any | None

    @property
    def present(self) -> bool:
        return self.config_map is not None

    @property
    def resource_version(self) -> str:
        if self.config_map is None:
            return ""
        return str(self.config_map.metadata.resource_version or "")


class ManifestWatch:
    """Keeps the latest wiring manifest observation, and wakes waiters on change."""

    def __init__(self, v1: Any, namespace: str, name: str) -> None:
        self._v1 = v1
        self._namespace = namespace
        self._name = name
        self._condition = threading.Condition()
        self._state: ManifestState | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="manifest-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def current(self, timeout: float | None = None) -> ManifestState | None:
        """The latest observation; waits up to ``timeout`` for the first LIST."""
        with self._condition:
            if self._state is None and timeout is not None:
                self._condition.wait_for(
                    lambda: self._state is not None or self._stop.is_set(), timeout
                )
            return self._state

    def wait_for_change(self, since: int, timeout: float) -> ManifestState | None:
        """Block until an observation newer than ``since`` exists, or ``timeout``."""
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    (self._state is not None and self._state.version > since) or self._stop.is_set()
                ),
                timeout,
            )
            return self._state

    def changed_since(self, version: int) -> bool:
        with self._condition:
            return self._state is not None and self._state.version != version

    def _publish(self, config_map: Any | None) -> None:
        with self._condition:
            previous = self._state
            if previous is not None and _same(previous.config_map, config_map):
                return
            self._state = ManifestState(
                version=(previous.version + 1) if previous is not None else 1,
                config_map=config_map,
            )
            self._condition.notify_all()

    def _run(self) -> None:
        import kubernetes.client
        import kubernetes.watch

        field_selector = f"metadata.name={self._name}"
        while not self._stop.is_set():
            try:
                listing = self._v1.list_namespaced_config_map(
                    self._namespace, field_selector=field_selector
                )
                items = list(listing.items or [])
                self._publish(items[0] if items else None)
                watch = kubernetes.watch.Watch()
                try:
                    for event in watch.stream(
                        self._v1.list_namespaced_config_map,
                        self._namespace,
                        field_selector=field_selector,
                        resource_version=listing.metadata.resource_version,
                        timeout_seconds=_WATCH_REQUEST_SECONDS,
                    ):
                        if self._stop.is_set():
                            break
                        kind = event["type"]
                        if kind == "DELETED":
                            self._publish(None)
                        elif kind in ("ADDED", "MODIFIED"):
                            self._publish(event["object"])
                finally:
                    watch.stop()
            except kubernetes.client.rest.ApiException as exc:
                if exc.status == 410:
                    # The watch fell behind the API server's history: list again.
                    continue
                log.warning("Wiring manifest watch refused (HTTP %s); listing again", exc.status)
                self._stop.wait(_RELIST_AFTER_ERROR_S)
            except Exception as exc:
                log.warning("Wiring manifest watch ended: %s; listing again", exc)
                self._stop.wait(_RELIST_AFTER_ERROR_S)


def _same(a: Any | None, b: Any | None) -> bool:
    if a is None or b is None:
        return a is b
    return (a.metadata.uid, a.metadata.resource_version) == (
        b.metadata.uid,
        b.metadata.resource_version,
    )
