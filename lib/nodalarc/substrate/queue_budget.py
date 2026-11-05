# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The kernel memory a session's link queues can hold on each server, against its budget.

Every link end holds two queues on its own server. One is the delay queue: on a local link the
netem queue of the transmitting pod interface, on a long-delay link the receive queue of the
end's host-side veth. The other is the rate queue of the end's other shaper. Each is counted at
its occupancy when its direction carries the platform's traffic envelope for the longest delay
the resolver bounded for the link: the delay queue holds the sustained load in flight plus a
burst, the rate queue a burst. Rejected alternative: counting each queue's packet limit. A local
link's limit is its terminal's line rate over its delay, about 2.7 GB of kernel memory for a
10 Gbit/s Earth-Moon link, which no link reaches under the envelope.

A terminal interface carries one link at a time, so an access interface is counted once, at the
longest delay of any candidate it can carry. A fixed long-delay link's ends serve one peer for
the whole session, and a contact that reopens while the last one drains reuses its queue: no
fixed terminal holds two contacts' queues at once.

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
from nodalarc.substrate.queue_sizing import TrafficEnvelope, envelope_occupancy_packets

# How many of a refused server's largest interfaces the refusal names.
_INTERFACES_NAMED = 8


@dataclass(frozen=True, slots=True)
class InterfaceQueueDemand:
    """The packets one link end's queues hold under the envelope, at its longest delay."""

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
    """The session's link queues would hold more memory on a server than its budget, or a
    server's capacity is unknown. The message names each server and its largest link ends."""


def session_queue_demand(
    resolved: ResolvedSession,
    placement: Mapping[str, str],
    *,
    envelope: TrafficEnvelope,
    bytes_per_packet: int,
) -> dict[str, ServerQueueDemand]:
    """The queue memory of every link end of ``resolved`` on the server ``placement`` puts its
    node on, by server."""
    longest_by_pair: dict[frozenset[str], float] = {}
    for candidate in resolved.link_candidates:
        key = frozenset(candidate.pair)
        longest_by_pair[key] = max(longest_by_pair.get(key, 0.0), candidate.longest_delay_s)
    by_server: dict[str, list[InterfaceQueueDemand]] = {}
    for (node_id, interface), peers in sorted(resolved.wan_interface_peers().items()):
        if not peers:
            continue
        server = placement.get(node_id)
        if server is None:
            raise ValueError(f"node {node_id} has link queues and no server in the placement")
        longest_s = max(longest_by_pair[frozenset((node_id, peer))] for peer in peers)
        delay_ms = longest_s * 1000.0
        packets = envelope_occupancy_packets(envelope, delay_ms) + envelope.burst_packets
        by_server.setdefault(server, []).append(
            InterfaceQueueDemand(
                node_id=node_id, interface=interface, longest_delay_s=longest_s, packets=packets
            )
        )
    return {
        server: ServerQueueDemand(
            server=server,
            bytes=sum(end.packets for end in ends) * bytes_per_packet,
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
) -> None:
    """Refuse a session whose queues exceed any server's budget, or that places queues on a
    server whose allocatable memory is unknown."""
    problems: list[str] = []
    for server, server_demand in sorted(demand.items()):
        allocatable = allocatable_bytes.get(server)
        if allocatable is None:
            problems.append(
                f"server {server} reports no allocatable memory, so its queue budget is unknown"
            )
            continue
        budget = allocatable * share
        if server_demand.bytes <= budget:
            continue
        named = ", ".join(
            f"{end.node_id}/{end.interface} ({end.longest_delay_s:,.1f} s, {end.packets:,} packets)"
            for end in server_demand.interfaces[:_INTERFACES_NAMED]
        )
        more = len(server_demand.interfaces) - _INTERFACES_NAMED
        problems.append(
            f"server {server} would hold up to {_mib(server_demand.bytes)} of link queues, over "
            f"its budget of {_mib(budget)} ({share:g} of {_mib(allocatable)} allocatable); the "
            f"largest link ends: {named}" + (f" and {more} more" if more > 0 else "")
        )
    if problems:
        raise QueueMemoryRefused("; ".join(problems))
