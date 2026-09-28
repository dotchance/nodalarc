# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The session writer lease: one Scheduler commands a session's kernel state.

A Scheduler takes this Lease before it sends any Node Agent command, and every
command carries the Lease's transition count as its writer epoch. Each change
of holder increments the count, so the Scheduler that took the Lease last
holds the highest epoch. A Node Agent that accepted a command at one epoch
refuses any lower epoch for the same session and wiring generation, so a
superseded Scheduler cannot change kernel state even while it still runs.

A second Scheduler of the same session waits as a standby while the holder
renews. A Scheduler of another session takes the Lease at once: the session
fence at the Node Agent already refuses the other session's commands.

Every write is a compare-and-swap on the Lease's resourceVersion, so of two
candidates exactly one takes the Lease.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import kubernetes.client
from nodalarc.substrate.manifest_contract import SCHEDULER_WRITER_LEASE as LEASE_NAME

log = logging.getLogger(__name__)

# A holder that has not renewed for this long has lost the Lease.
LEASE_DURATION_S = 15
# The holder renews this often.
RENEW_INTERVAL_S = 5.0
# A holder that could not renew for this long stops commanding: after
# LEASE_DURATION_S another Scheduler may take the Lease.
RENEW_DEADLINE_S = 10.0
# A standby looks at the Lease this often.
STANDBY_RETRY_S = 2.0
# Writes that lose the compare-and-swap in a row before a renewal or release
# counts as failed.
_WRITE_ATTEMPTS = 3


class WriterLeaseConflict(RuntimeError):
    """Every write in a row lost the Lease's compare-and-swap."""


class WriterLeaseLost(Exception):
    """This Scheduler can no longer prove it holds the Lease, and no successor was seen.

    The process exits and the kubelet restarts it: the new process takes the
    Lease at a higher epoch, once no other holder renews it.
    """


class WriterLease:
    """This Scheduler's claim on the session writer Lease."""

    def __init__(
        self,
        api: kubernetes.client.CoordinationV1Api,
        namespace: str,
        *,
        session_id: str,
        instance: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not session_id or "/" in session_id:
            raise ValueError(f"invalid session id for the writer lease: {session_id!r}")
        self._api = api
        self._namespace = namespace
        self._session_id = session_id
        # The session names the holder's scope; the instance names this
        # process, so a restarted container is a new holder with a new epoch.
        self.holder = f"{session_id}/{instance}"
        self._now = now
        self.epoch: int | None = None

    def try_acquire(self) -> int | None:
        """Take the Lease and return the writer epoch, or None while another holder has it.

        The Lease is taken when it is absent, released, expired, or held for
        another session. None means a Scheduler of this session holds it and
        renews it, or another candidate took it first.
        """
        try:
            lease = self._api.read_namespaced_lease(LEASE_NAME, self._namespace)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status != 404:
                raise
            return self._create()
        spec = lease.spec
        holder = spec.holder_identity or ""
        if holder and holder.split("/", 1)[0] == self._session_id and not self._expired(spec):
            return None
        epoch = (spec.lease_transitions or 0) + 1
        lease.spec = self._held_spec(epoch)
        try:
            self._api.replace_namespaced_lease(LEASE_NAME, self._namespace, lease)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 409:
                return None
            raise
        self.epoch = epoch
        return epoch

    def renew(self) -> bool:
        """Extend the hold; False once the Lease names another holder.

        A write that loses the compare-and-swap reads the Lease again and
        retries. Raises WriterLeaseConflict when every attempt lost, so the
        caller counts the renewal as failed: renewTime was not extended.
        """
        for _attempt in range(_WRITE_ATTEMPTS):
            lease = self._api.read_namespaced_lease(LEASE_NAME, self._namespace)
            if lease.spec.holder_identity != self.holder:
                return False
            lease.spec.renew_time = self._now()
            try:
                self._api.replace_namespaced_lease(LEASE_NAME, self._namespace, lease)
            except kubernetes.client.rest.ApiException as exc:
                if exc.status != 409:
                    raise
                continue
            return True
        raise WriterLeaseConflict(
            f"Lease {LEASE_NAME} renewal lost {_WRITE_ATTEMPTS} writes in a row"
        )

    def release(self) -> None:
        """Give the Lease up so a successor takes it without waiting for expiry.

        Raises WriterLeaseConflict when every write lost the compare-and-swap;
        the Lease then expires on its own.
        """
        if self.epoch is None:
            return
        for _attempt in range(_WRITE_ATTEMPTS):
            lease = self._api.read_namespaced_lease(LEASE_NAME, self._namespace)
            if lease.spec.holder_identity != self.holder:
                return
            lease.spec.holder_identity = None
            lease.spec.renew_time = self._now()
            try:
                self._api.replace_namespaced_lease(LEASE_NAME, self._namespace, lease)
            except kubernetes.client.rest.ApiException as exc:
                if exc.status != 409:
                    raise
                continue
            return
        raise WriterLeaseConflict(
            f"Lease {LEASE_NAME} release lost {_WRITE_ATTEMPTS} writes in a row"
        )

    def _create(self) -> int | None:
        lease = kubernetes.client.V1Lease(
            metadata=kubernetes.client.V1ObjectMeta(name=LEASE_NAME, namespace=self._namespace),
            spec=self._held_spec(1),
        )
        try:
            self._api.create_namespaced_lease(self._namespace, lease)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 409:
                return None
            raise
        self.epoch = 1
        return 1

    def _held_spec(self, epoch: int) -> kubernetes.client.V1LeaseSpec:
        now = self._now()
        return kubernetes.client.V1LeaseSpec(
            holder_identity=self.holder,
            lease_duration_seconds=LEASE_DURATION_S,
            acquire_time=now,
            renew_time=now,
            lease_transitions=epoch,
        )

    def _expired(self, spec: kubernetes.client.V1LeaseSpec) -> bool:
        if spec.renew_time is None:
            return True
        duration = timedelta(seconds=spec.lease_duration_seconds or LEASE_DURATION_S)
        return spec.renew_time + duration < self._now()
