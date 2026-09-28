# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Scheduler writer Lease as this Node Agent last observed it.

The Scheduler that holds the Lease commands the session, and the Lease's
transition count is the writer epoch its commands carry. The count rises each
time the Lease changes holder, so a command with a lower epoch comes from a
Scheduler that no longer holds it. The view is kept current by a LIST, then a
WATCH (``nodalarc.kube_watch.list_then_watch``); a WATCH that ends, expires or
loses its connection is followed by a new LIST.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from nodalarc.kube_watch import list_then_watch
from nodalarc.substrate.manifest_contract import SCHEDULER_WRITER_LEASE

log = logging.getLogger(__name__)

# How long one WATCH request stays open before the API server ends it.
_WATCH_REQUEST_SECONDS = 300
# Pause before listing again after the API server refused a request.
_RELIST_AFTER_ERROR_S = 1.0


@dataclass(frozen=True, slots=True)
class LeaseEpoch:
    """One observation of the Lease: which object, and its transition count."""

    # A recreated Lease is another object whose count starts again.
    uid: str
    transitions: int


class WriterLeaseNotObserved(RuntimeError):
    """The Lease was asked for before the first LIST answered."""


class WriterLeaseView:
    """Keeps the latest observation of the Scheduler writer Lease."""

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._lock = threading.Lock()
        self._listed = threading.Event()
        self._epoch: LeaseEpoch | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, coordination_v1: Any) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(coordination_v1,), name="writer-lease-view", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait_listed(self, timeout: float) -> bool:
        """Wait up to ``timeout`` for the first LIST; False when it never answered."""
        return self._listed.wait(timeout)

    def current(self) -> LeaseEpoch | None:
        """The Lease as last observed; None when it does not exist.

        Raises WriterLeaseNotObserved before the first LIST answered.
        """
        if not self._listed.is_set():
            raise WriterLeaseNotObserved(
                f"the Lease {SCHEDULER_WRITER_LEASE} has not been observed yet"
            )
        with self._lock:
            return self._epoch

    def _publish(self, lease: Any | None) -> None:
        epoch = None
        if lease is not None:
            epoch = LeaseEpoch(
                uid=str(lease.metadata.uid or ""),
                transitions=int(lease.spec.lease_transitions or 0),
            )
        with self._lock:
            if epoch != self._epoch:
                log.info("Scheduler writer Lease observed: %s", epoch)
            self._epoch = epoch
        self._listed.set()

    def _run(self, coordination_v1: Any) -> None:
        import kubernetes.client

        while not self._stop.is_set():
            try:
                for leases in list_then_watch(
                    coordination_v1.list_namespaced_lease,
                    request_seconds=lambda: _WATCH_REQUEST_SECONDS,
                    stopped=self._stop.is_set,
                    namespace=self._namespace,
                    field_selector=f"metadata.name={SCHEDULER_WRITER_LEASE}",
                ):
                    self._publish(leases.get(SCHEDULER_WRITER_LEASE))
            except kubernetes.client.rest.ApiException as exc:
                log.warning("Writer Lease watch refused (HTTP %s); listing again", exc.status)
                self._stop.wait(_RELIST_AFTER_ERROR_S)
            except Exception as exc:
                log.warning("Writer Lease watch ended: %s; listing again", exc)
                self._stop.wait(_RELIST_AFTER_ERROR_S)
