# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The long-delay queue capacity estimate: the kernel memory a session's long-delay link queues
can hold on each server, against the server's budget.

A long-delay link end holds two queues on its own server: the receive queue of its host-side
veth, which delays what the end receives, and the transmit leaf behind its pod interface's rate
shaper, which the stop purges. The receive queue is counted at its occupancy when its direction
carries the platform's traffic envelope for the longest delay the resolver bounded for the link:
the sustained load in flight plus a burst. The transmit leaf is counted full, at the limit the
Node Agent gives it.

Each queued buffer is counted at one installation figure (``bytes_per_buffer``). It covers
buffers up to 64 KiB, the GSO and GRO limit, on servers with 4 KiB pages; node qualification
refuses a server whose page size or limits need more. The result is an estimate. It does not
count memory a buffer keeps allocated through a shared page block, allocator fragmentation, or
anything else the server runs, so it cannot guarantee that a server never runs out of memory.

Local link queues are not part of the estimate. Each is bounded by its own packet limit
(``queue_sizing.queue_limit_packets``), as it was before long-delay links existed. Rejected
alternative: counting local queues at the same per-buffer figure, which refuses most Earth and
Earth-Luna sessions on servers that run them (a 176-satellite Walker session needs an estimated
11.6 GiB on each of three servers).

A terminal interface carries one link at a time, so an access interface is counted once, at the
longest delay of any long-delay candidate it can carry. A fixed long-delay link's ends serve one
peer for the whole session, and a contact that reopens while the last one drains reuses its
queue: no fixed terminal holds two contacts' queues at once.

The budget of a server is a share of its Kubernetes allocatable memory, an installation setting.
Allocatable memory is what Kubernetes offers pods on the server (its capacity less system
reservations), not the memory unused at the moment of the check; the share leaves room for every
other consumer. A server whose capacity Kubernetes does not report is a refusal: its budget is
unknown. The same function checks the placement the Operator computes before it creates pods and
the placement it observes before it publishes wiring, so a session whose pods land elsewhere
than computed is checked where they are.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.substrate.queue_sizing import (
    LONG_DELAY_TRANSMIT_LEAF_PACKETS,
    TrafficEnvelope,
    envelope_occupancy_packets,
)

# How many of a refused server's largest long-delay ends the refusal names.
_INTERFACES_NAMED = 8


@dataclass(frozen=True, slots=True)
class InterfaceQueueDemand:
    """The buffers one long-delay end's queues hold under the envelope, at its longest delay."""

    node_id: str
    interface: str
    longest_delay_s: float
    packets: int


@dataclass(frozen=True, slots=True)
class ServerQueueDemand:
    server: str
    bytes: int
    interfaces: tuple[InterfaceQueueDemand, ...]


class QueueMemoryRefused(ValueError):
    """The session's long-delay queues would hold more memory on a server than its budget by the
    estimate, or a server's capacity is unknown. The message names each server, its largest
    long-delay ends and the installation settings that change the result."""


def long_delay_queue_demand(
    resolved: ResolvedSession,
    placement: Mapping[str, str],
    *,
    envelope: TrafficEnvelope,
    bytes_per_buffer: int,
) -> dict[str, ServerQueueDemand]:
    """The estimated queue memory of every long-delay end of ``resolved`` on the server
    ``placement`` puts its node on, by server. A session without long-delay links has none."""
    longest_by_pair: dict[frozenset[str], float] = {}
    for candidate in resolved.link_candidates:
        if candidate.delay_class != "long_delay":
            continue
        key = frozenset(candidate.pair)
        longest_by_pair[key] = max(longest_by_pair.get(key, 0.0), candidate.longest_delay_s)
    by_server: dict[str, list[InterfaceQueueDemand]] = {}
    for (node_id, interface), peers in sorted(resolved.wan_interface_peers().items()):
        pairs = [frozenset((node_id, peer)) for peer in peers]
        delays = [longest_by_pair[pair] for pair in pairs if pair in longest_by_pair]
        if not delays:
            continue
        server = placement.get(node_id)
        if server is None:
            raise ValueError(f"node {node_id} has long-delay queues and no server in the placement")
        longest_s = max(delays)
        packets = (
            envelope_occupancy_packets(envelope, longest_s * 1000.0)
            + LONG_DELAY_TRANSMIT_LEAF_PACKETS
        )
        by_server.setdefault(server, []).append(
            InterfaceQueueDemand(
                node_id=node_id, interface=interface, longest_delay_s=longest_s, packets=packets
            )
        )
    return {
        server: ServerQueueDemand(
            server=server,
            bytes=sum(end.packets for end in ends) * bytes_per_buffer,
            interfaces=tuple(
                sorted(ends, key=lambda end: (-end.packets, end.node_id, end.interface))
            ),
        )
        for server, ends in sorted(by_server.items())
    }


def _mib(value: float) -> str:
    return f"{value / 2**20:,.1f} MiB"


def check_queue_budget(
    demand: Mapping[str, ServerQueueDemand],
    *,
    allocatable_bytes: Mapping[str, int],
    share: float,
    bytes_per_buffer: int,
) -> None:
    """Refuse a session whose estimated long-delay queues exceed any server's budget, or that
    places them on a server whose allocatable memory is unknown."""
    problems: list[str] = []
    for server, server_demand in sorted(demand.items()):
        allocatable = allocatable_bytes.get(server)
        if allocatable is None:
            problems.append(
                f"server {server} reports no allocatable memory, so its budget for long-delay "
                "queues is unknown"
            )
            continue
        budget = allocatable * share
        if server_demand.bytes <= budget:
            continue
        named = ", ".join(
            f"{end.node_id}/{end.interface} ({end.longest_delay_s:,.1f} s, {end.packets:,} buffers)"
            for end in server_demand.interfaces[:_INTERFACES_NAMED]
        )
        more = len(server_demand.interfaces) - _INTERFACES_NAMED
        problems.append(
            f"server {server} needs an estimated {_mib(server_demand.bytes)} for long-delay "
            f"queues at {bytes_per_buffer:,} bytes per buffer, over its budget of "
            f"{_mib(budget)} ({share:g} of {_mib(allocatable)} allocatable); the long-delay "
            f"ends: {named}" + (f" and {more} more" if more > 0 else "")
        )
    if problems:
        raise QueueMemoryRefused(
            "long-delay queue capacity estimate: " + "; ".join(problems) + ". The installation "
            "settings network.longDelayEnvelope.* (queue sizes) and "
            "network.queueMemory.serverShare (budget) change this result."
        )
