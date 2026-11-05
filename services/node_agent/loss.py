# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Counting the packets a queue held when its device was reset.

A device's reset (its carrier going down, or the device going down) discards whatever its
queueing discipline holds, and netem counts none of it as dropped. The count comes from the
device's root qdisc instead, read before and after the reset: the root qdisc keeps its sent and
dropped counters through the reset (measured on kernel 6.8). While nothing enters the device,
held + sent + dropped stays constant, so

    lost at reset = (held + sent + dropped) before - (sent + dropped) after.

Nothing enters once admission is stopped upstream: on the transmitting interface for a local
link's delay queue, and on the peer's transmitting interface for a long-delay link's receive-side
queue. Rejected alternative: reading the queue length just before the reset. Packets leave and
arrive between that read and the reset, so the reading is a sample of the loss, not its count.
Measured on a local link with a 2 s delay at 100 datagrams/s: 169 lost at reset by this count
and 401 taken by admission, against 570 numbered datagrams that never arrived.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyroute2 import IPRoute
from pyroute2.netlink.rtnl import TC_H_ROOT

from node_agent.namespace_ops import _in_namespace, in_host_namespace
from node_agent.tc_dump import interface_qdiscs


@dataclass(frozen=True, slots=True)
class QueueCounts:
    """A device's root qdisc: packets it holds, has sent and has dropped."""

    held: int
    sent: int
    dropped: int

    @property
    def total(self) -> int:
        return self.held + self.sent + self.dropped


def _root_counts(ipr: IPRoute, ifname: str) -> QueueCounts:
    found = ipr.link_lookup(ifname=ifname)
    if not found:
        raise FileNotFoundError(f"interface {ifname} not found")
    roots = [q for q in interface_qdiscs(found[0]) if q["parent"] == TC_H_ROOT]
    if len(roots) != 1:
        raise RuntimeError(f"{ifname} has {len(roots)} root qdiscs; one is expected")
    stats = roots[0].get_attr("TCA_STATS2")
    if stats is None:
        raise RuntimeError(f"the root qdisc of {ifname} reports no statistics")
    basic = stats.get_attr("TCA_STATS_BASIC")
    queue = stats.get_attr("TCA_STATS_QUEUE")
    return QueueCounts(
        held=int(queue["qlen"]), sent=int(basic["packets"]), dropped=int(queue["drops"])
    )


@dataclass(frozen=True, slots=True)
class QueueDevice:
    """The device holding a link end's delay queue: a pod interface (``pid`` set) or a host
    device (``pid`` None)."""

    ifname: str
    pid: int | None

    def counts(self) -> QueueCounts:
        if self.pid is None:
            return in_host_namespace(lambda ipr: _root_counts(ipr, self.ifname))
        return _in_namespace(self.pid, lambda ipr: _root_counts(ipr, self.ifname))


def counted_reset(devices: list[QueueDevice], reset: Callable[[], None]) -> list[int]:
    """Run ``reset`` and return, per device, the packets it discarded from that device's
    queues. The caller has stopped everything that feeds the devices."""
    before = [device.counts() for device in devices]
    reset()
    lost = []
    for device, held in zip(devices, before, strict=True):
        after = device.counts()
        count = held.total - after.sent - after.dropped
        if count < 0:
            raise RuntimeError(
                f"{device.ifname} took in packets during a counted reset: before {held}, "
                f"after {after}"
            )
        lost.append(count)
    return lost
