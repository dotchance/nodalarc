# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The session writer lease: one Scheduler commands a session at a time."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta

import kubernetes
import pytest
from scheduler import __main__ as scheduler_main
from scheduler.dispatcher import DispatcherSuperseded
from scheduler.writer_lease import (
    LEASE_DURATION_S,
    LEASE_NAME,
    WriterLease,
    WriterLeaseConflict,
    WriterLeaseLost,
)

NAMESPACE = "nodalarc"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _LeaseApi:
    """One Lease with the API server's compare-and-swap on resourceVersion."""

    def __init__(self) -> None:
        self.lease: kubernetes.client.V1Lease | None = None
        self.version = 0

    def read_namespaced_lease(self, name, namespace):
        assert (name, namespace) == (LEASE_NAME, NAMESPACE)
        if self.lease is None:
            raise kubernetes.client.rest.ApiException(status=404, reason="Not Found")
        return copy.deepcopy(self.lease)

    def create_namespaced_lease(self, namespace, body):
        if self.lease is not None:
            raise kubernetes.client.rest.ApiException(status=409, reason="AlreadyExists")
        self._store(body)

    def replace_namespaced_lease(self, name, namespace, body):
        if body.metadata.resource_version != str(self.version):
            raise kubernetes.client.rest.ApiException(status=409, reason="Conflict")
        self._store(body)

    def _store(self, body) -> None:
        self.version += 1
        stored = copy.deepcopy(body)
        stored.metadata.resource_version = str(self.version)
        self.lease = stored

    @property
    def holder(self) -> str | None:
        return self.lease.spec.holder_identity if self.lease else None


def _lease(api, clock, *, session="run-a", instance="pod-1/t1") -> WriterLease:
    return WriterLease(api, NAMESPACE, session_id=session, instance=instance, now=clock)


def test_the_first_scheduler_creates_the_lease_at_epoch_one() -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    assert lease.try_acquire() == 1
    assert api.holder == "run-a/pod-1/t1"
    assert lease.epoch == 1


def test_a_standby_of_the_same_session_waits_until_the_holder_stops_renewing() -> None:
    api, clock = _LeaseApi(), _Clock()
    active = _lease(api, clock, instance="pod-1/t1")
    standby = _lease(api, clock, instance="pod-2/t2")
    assert active.try_acquire() == 1

    assert standby.try_acquire() is None
    clock.advance(LEASE_DURATION_S - 1)
    assert active.renew() is True
    clock.advance(LEASE_DURATION_S - 1)
    assert standby.try_acquire() is None

    clock.advance(2)
    assert standby.try_acquire() == 2
    assert active.renew() is False


def test_a_scheduler_of_the_next_session_takes_the_lease_at_once() -> None:
    api, clock = _LeaseApi(), _Clock()
    old = _lease(api, clock, session="run-a")
    new = _lease(api, clock, session="run-b", instance="pod-9/t9")
    assert old.try_acquire() == 1
    assert new.try_acquire() == 2
    assert api.holder == "run-b/pod-9/t9"
    assert old.renew() is False


def test_a_released_lease_passes_on_without_waiting_and_the_epoch_rises() -> None:
    api, clock = _LeaseApi(), _Clock()
    first = _lease(api, clock, instance="pod-1/t1")
    second = _lease(api, clock, instance="pod-1/t2")
    assert first.try_acquire() == 1
    first.release()
    assert api.holder is None
    assert second.try_acquire() == 2


def test_release_never_takes_the_lease_from_a_later_holder() -> None:
    api, clock = _LeaseApi(), _Clock()
    old = _lease(api, clock, session="run-a")
    new = _lease(api, clock, session="run-b", instance="pod-9/t9")
    old.try_acquire()
    new.try_acquire()
    old.release()
    assert api.holder == "run-b/pod-9/t9"


def test_of_two_racing_candidates_exactly_one_takes_the_lease() -> None:
    api, clock = _LeaseApi(), _Clock()
    holder = _lease(api, clock, session="run-a")
    holder.try_acquire()
    a = _lease(api, clock, session="run-b", instance="pod-a/ta")
    b = _lease(api, clock, session="run-b", instance="pod-b/tb")

    # Both read the lease before either writes it.
    read = api.read_namespaced_lease
    snapshot = read(LEASE_NAME, NAMESPACE)
    api.read_namespaced_lease = lambda *_: copy.deepcopy(snapshot)
    assert a.try_acquire() == 2
    assert b.try_acquire() is None
    api.read_namespaced_lease = read
    assert api.holder == "run-b/pod-a/ta"


# ---------------------------------------------------------------------------
# The Scheduler process: hold the lease while commanding, give it up after
# ---------------------------------------------------------------------------


class _Dispatcher:
    def __init__(self, epoch: int, *, runs_until_stopped: bool = True, raises=None) -> None:
        self.epoch = epoch
        self.stopped = asyncio.Event()
        self.superseded: str | None = None
        self._runs_until_stopped = runs_until_stopped
        self._raises = raises

    async def run(self) -> None:
        if self._raises is not None:
            raise self._raises
        if self._runs_until_stopped:
            await self.stopped.wait()
        if self.superseded is not None:
            raise DispatcherSuperseded(self.superseded)

    def stop(self) -> None:
        self.stopped.set()

    def supersede(self, reason: str) -> None:
        self.superseded = reason
        self.stop()


@pytest.fixture
def fast_renewal(monkeypatch):
    monkeypatch.setattr(scheduler_main, "RENEW_INTERVAL_S", 0.01)
    monkeypatch.setattr(scheduler_main, "RENEW_DEADLINE_S", 0.1)
    monkeypatch.setattr(scheduler_main, "STANDBY_RETRY_S", 0.01)


def test_losing_the_lease_supersedes_the_dispatcher(fast_renewal) -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    built: list[_Dispatcher] = []

    def _build(epoch: int) -> _Dispatcher:
        built.append(_Dispatcher(epoch))
        return built[-1]

    async def _scenario():
        serving = asyncio.ensure_future(
            scheduler_main._serve(lease, _build, commanding=lambda: None)
        )
        await asyncio.sleep(0.05)
        # Another session's Scheduler takes the lease.
        _lease(api, clock, session="run-b", instance="pod-9/t9").try_acquire()
        await serving

    with pytest.raises(DispatcherSuperseded, match="now names another Scheduler"):
        asyncio.run(_scenario())
    assert built[0].epoch == 1
    # The lease stays with its new holder.
    assert api.holder == "run-b/pod-9/t9"


def test_an_unrenewable_lease_supersedes_after_the_renew_deadline(fast_renewal) -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    built: list[_Dispatcher] = []

    def _build(epoch: int) -> _Dispatcher:
        built.append(_Dispatcher(epoch))
        return built[-1]

    def _unreachable(*_args):
        raise kubernetes.client.rest.ApiException(status=503, reason="Unavailable")

    async def _scenario():
        serving = asyncio.ensure_future(
            scheduler_main._serve(lease, _build, commanding=lambda: None)
        )
        await asyncio.sleep(0.03)
        api.read_namespaced_lease = _unreachable
        await serving

    # The hold is unproven, not taken by a successor: the process exits for a restart.
    with pytest.raises(WriterLeaseLost, match="not renewed"):
        asyncio.run(_scenario())
    assert built[0].stopped.is_set()
    assert built[0].superseded is None


def test_a_stopped_scheduler_releases_the_lease(fast_renewal) -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    built: list[_Dispatcher] = []

    def _build(epoch: int) -> _Dispatcher:
        built.append(_Dispatcher(epoch))
        return built[-1]

    async def _scenario():
        serving = asyncio.ensure_future(
            scheduler_main._serve(lease, _build, commanding=lambda: None)
        )
        await asyncio.sleep(0.03)
        built[0].stop()
        await serving

    asyncio.run(_scenario())
    assert api.holder is None


def test_a_superseded_scheduler_releases_the_lease_it_still_holds(fast_renewal) -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)

    def _build(epoch: int) -> _Dispatcher:
        return _Dispatcher(epoch, raises=DispatcherSuperseded("manifest moved on"))

    with pytest.raises(DispatcherSuperseded):
        asyncio.run(scheduler_main._serve(lease, _build, commanding=lambda: None))
    assert api.holder is None


def test_a_standby_starts_commanding_once_the_holder_releases(fast_renewal) -> None:
    api, clock = _LeaseApi(), _Clock()
    active = _lease(api, clock, instance="pod-1/t1")
    active.try_acquire()
    standby = _lease(api, clock, instance="pod-2/t2")
    built: list[_Dispatcher] = []
    commanding: list[int] = []

    def _build(epoch: int) -> _Dispatcher:
        built.append(_Dispatcher(epoch))
        return built[-1]

    async def _scenario():
        serving = asyncio.ensure_future(
            scheduler_main._serve(standby, _build, commanding=lambda: commanding.append(1))
        )
        await asyncio.sleep(0.05)
        # A standby is not ready: it commands nothing.
        assert built == []
        assert commanding == []
        active.release()
        for _ in range(100):
            if built:
                break
            await asyncio.sleep(0.01)
        built[0].stop()
        await serving

    asyncio.run(_scenario())
    assert built[0].epoch == 2
    assert commanding == [1]


# ---------------------------------------------------------------------------
# Writes are compare-and-swap: a lost write reads the Lease again
# ---------------------------------------------------------------------------


def _conflicting(api: _LeaseApi, times: int) -> None:
    """The next ``times`` writes lose the compare-and-swap to a write that keeps the holder."""
    replace = api.replace_namespaced_lease

    def _replace(name, namespace, body):
        nonlocal times
        if times > 0:
            times -= 1
            # Another writer's update landed first.
            api.version += 1
            api.lease.metadata.resource_version = str(api.version)
            raise kubernetes.client.rest.ApiException(status=409, reason="Conflict")
        return replace(name, namespace, body)

    api.replace_namespaced_lease = _replace


def test_a_renewal_that_lost_a_write_retries_and_extends_the_hold() -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    lease.try_acquire()
    clock.advance(5)
    _conflicting(api, times=1)
    assert lease.renew() is True
    assert api.lease.spec.renew_time == clock.now


def test_a_renewal_that_loses_every_write_raises_and_extends_nothing() -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    lease.try_acquire()
    acquired_at = api.lease.spec.renew_time
    clock.advance(5)
    _conflicting(api, times=3)
    with pytest.raises(WriterLeaseConflict):
        lease.renew()
    assert api.lease.spec.renew_time == acquired_at


def test_a_release_that_lost_a_write_retries() -> None:
    api, clock = _LeaseApi(), _Clock()
    lease = _lease(api, clock)
    lease.try_acquire()
    _conflicting(api, times=1)
    lease.release()
    assert api.holder is None


def test_the_chart_grants_access_to_the_lease_the_code_names() -> None:
    """The Scheduler's and the Node Agent's Roles name the Lease by the one constant."""
    from pathlib import Path

    from nodalarc.substrate.manifest_contract import SCHEDULER_WRITER_LEASE

    templates = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "templates"
    for template in ("management-network.yaml", "node-agent-rbac.yaml"):
        text = (templates / template).read_text()
        assert f'resourceNames: ["{SCHEDULER_WRITER_LEASE}"]' in text, template
