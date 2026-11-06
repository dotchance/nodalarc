# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The long-delay queue capacity estimate: the kernel memory a session's long-delay link queues
can hold on each server, against the server's budget.

A long-delay link end holds two queues on its own server.

The receive queue of its host-side veth delays what the peer sends. The peer's rate shaper
releases at most the peer terminal's configured transmit rate, so the queue holds at most that
rate times the link's longest delay (the resolver bounds it over the session's ephemeris
coverage), plus the shaper's burst (``queue_sizing.shaper_burst_bytes``), plus the queue's buffer
of ``queue_sizing.BUFFER_PACKETS`` frames at the link MTU. The kernel accounts more memory for a
queued frame than its length: on kernel 6.8, 1.52 times a 1,514-byte frame, 1.89 times an
8,814-byte frame, 1.015 times a 65,000-byte GSO buffer. The estimate counts every byte at
``RECEIVE_QUEUE_OVERHEAD``, above every measured ratio, to leave room for frame sizes not measured.
Rejected alternative: measuring the ratio on each server at start, since a local probe cannot
establish the accounting of NIC-dependent allocations.

The transmit leaf sits before the rate shaper, so the rate does not bound what it holds, and
buffers up to the GSO and GRO limit can enter it. It is counted full, at
``queue_sizing.LONG_DELAY_TRANSMIT_LEAF_PACKETS`` buffers of the installation's per-buffer figure,
which node qualification checks against each server's GSO and GRO limits.

The terminals' rates come from the session's terminal files, so a terminal changed to 256 kbit/s
or 100 Mbit/s changes the estimate with its queues. The result is an estimate: it does not count
memory a buffer keeps through a shared page block, allocator fragmentation, or anything else the
server runs.

Local link queues are not part of the estimate. Each is bounded by its own packet limit
(``queue_sizing.queue_limit_packets``), as it was before long-delay links existed.

An access interface may carry any of its node's access candidates (OME assigns the interface per
contact), so it is counted for the long-delay peer whose queue would be largest there, sent from
the fastest access terminal that peer has. A fixed long-delay link's ends serve one peer for the
whole session, and a contact that reopens while the last one drains reuses its queue.

The budget of a server is a share of its Kubernetes allocatable memory, an installation setting.
Allocatable memory is what Kubernetes offers pods on the server (its capacity less system
reservations), not the memory unused at the moment of the check; the share leaves room for every
other consumer. Queued packets are charged to no pod: netem detaches a delayed packet from its
sending socket, so raising a pod's memory limit adds no room. A server whose capacity Kubernetes
does not report is a refusal: its budget is unknown. The same function checks the placement the
Operator computes before it creates pods and the placement it observes before it publishes
wiring, so a session whose pods land elsewhere than computed is checked where they are.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.substrate.queue_sizing import (
    BUFFER_PACKETS,
    LONG_DELAY_TRANSMIT_LEAF_PACKETS,
    bytes_in_flight,
    shaper_burst_bytes,
)

# Kernel memory counted per byte a long-delay receive queue can hold. Measured on kernel 6.8: 1.52
# at 1,514-byte frames, 1.89 at 8,814-byte frames, 1.015 at a 65,000-byte GSO buffer.
RECEIVE_QUEUE_OVERHEAD = 2.5
# How many of a refused server's largest long-delay ends the refusal names.
_INTERFACES_NAMED = 8


@dataclass(frozen=True, slots=True)
class InterfaceQueueDemand:
    """One long-delay end's queues: the peer whose sending fills its receive queue, that peer's
    terminal and configured rate, the link's longest delay, the payload in flight, and the memory
    the estimate counts for the receive queue and the transmit leaf together."""

    node_id: str
    interface: str
    peer_node_id: str
    peer_interface: str
    peer_terminal: str
    peer_transmit_mbps: float
    longest_delay_s: float
    payload_bytes: int
    charged_bytes: int


@dataclass(frozen=True, slots=True)
class ServerQueueDemand:
    server: str
    bytes: int
    interfaces: tuple[InterfaceQueueDemand, ...]


class QueueMemoryRefused(ValueError):
    """The session's long-delay queues would hold more memory on a server than its budget by the
    estimate, or a server's capacity is unknown. The message names each server, its largest
    long-delay ends with their peers' terminals and rates, and what changes the result."""


@dataclass(frozen=True, slots=True)
class _Sender:
    """A terminal that can send into one long-delay end's receive queue."""

    node_id: str
    interface: str
    terminal: str
    transmit_mbps: float
    longest_delay_s: float


def _senders(resolved: ResolvedSession) -> dict[tuple[str, str], list[_Sender]]:
    """For each long-delay end, every peer terminal that can send into its receive queue."""
    nodes = {node.node_id: node for node in resolved.nodes}

    def sender(peer: str, interface: str, longest_delay_s: float) -> _Sender:
        block = nodes[peer].wan_terminal(interface)
        return _Sender(peer, interface, block.source_ref, block.transmit_mbps, longest_delay_s)

    senders: dict[tuple[str, str], list[_Sender]] = {}
    for candidate in resolved.link_candidates:
        if candidate.delay_class != "long_delay":
            continue
        a, b = candidate.pair
        if candidate.kind == "access":
            for node_id, peer in ((a, b), (b, a)):
                for interface in nodes[node_id].access_interfaces:
                    senders.setdefault((node_id, interface), []).extend(
                        sender(peer, peer_interface, candidate.longest_delay_s)
                        for peer_interface in nodes[peer].access_interfaces
                    )
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        senders.setdefault((a, interface_a), []).append(
            sender(b, interface_b, candidate.longest_delay_s)
        )
        senders.setdefault((b, interface_b), []).append(
            sender(a, interface_a, candidate.longest_delay_s)
        )
    return senders


def _receive_charge(sender: _Sender, mtu_bytes: int) -> tuple[int, int]:
    """The payload a sender has in flight over its link's longest delay, and the memory the
    estimate counts for the receive queue holding it: the payload, the sender's shaper burst and
    the queue's buffer frames at the MTU, at the kernel overhead."""
    payload = round(bytes_in_flight(sender.transmit_mbps, sender.longest_delay_s * 1000.0))
    held = (
        payload + shaper_burst_bytes(sender.transmit_mbps, mtu_bytes) + BUFFER_PACKETS * mtu_bytes
    )
    return payload, round(held * RECEIVE_QUEUE_OVERHEAD)


def long_delay_queue_demand(
    resolved: ResolvedSession,
    placement: Mapping[str, str],
    *,
    mtu_bytes: int,
    bytes_per_buffer: int,
) -> dict[str, ServerQueueDemand]:
    """The estimated queue memory of every long-delay end of ``resolved`` on the server
    ``placement`` puts its node on, by server. A session without long-delay links has none."""
    leaf = LONG_DELAY_TRANSMIT_LEAF_PACKETS * bytes_per_buffer
    by_server: dict[str, list[InterfaceQueueDemand]] = {}
    for (node_id, interface), senders in sorted(_senders(resolved).items()):
        server = placement.get(node_id)
        if server is None:
            raise ValueError(f"node {node_id} has long-delay queues and no server in the placement")
        charges = [(_receive_charge(sender, mtu_bytes), sender) for sender in senders]
        (payload, receive), sender = max(
            charges, key=lambda item: (item[0][1], item[1].node_id, item[1].interface)
        )
        by_server.setdefault(server, []).append(
            InterfaceQueueDemand(
                node_id=node_id,
                interface=interface,
                peer_node_id=sender.node_id,
                peer_interface=sender.interface,
                peer_terminal=sender.terminal,
                peer_transmit_mbps=sender.transmit_mbps,
                longest_delay_s=sender.longest_delay_s,
                payload_bytes=payload,
                charged_bytes=receive + leaf,
            )
        )
    return {
        server: ServerQueueDemand(
            server=server,
            bytes=sum(end.charged_bytes for end in ends),
            interfaces=tuple(
                sorted(ends, key=lambda end: (-end.charged_bytes, end.node_id, end.interface))
            ),
        )
        for server, ends in sorted(by_server.items())
    }


def _mib(value: float) -> str:
    return f"{value / 2**20:,.1f} MiB"


def _end_text(end: InterfaceQueueDemand) -> str:
    return (
        f"{end.node_id}/{end.interface} receiving from {end.peer_node_id}/{end.peer_interface} "
        f"({end.peer_terminal} at {end.peer_transmit_mbps:g} Mbit/s, longest delay "
        f"{end.longest_delay_s:,.1f} s: {_mib(end.payload_bytes)} in flight, "
        f"{_mib(end.charged_bytes)} counted)"
    )


def check_queue_budget(
    demand: Mapping[str, ServerQueueDemand],
    *,
    allocatable_bytes: Mapping[str, int],
    share: float,
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
        named = "; ".join(_end_text(end) for end in server_demand.interfaces[:_INTERFACES_NAMED])
        more = len(server_demand.interfaces) - _INTERFACES_NAMED
        problems.append(
            f"server {server} needs an estimated {_mib(server_demand.bytes)} for long-delay "
            f"queues, over its budget of {_mib(budget)} ({share:g} of {_mib(allocatable)} "
            f"allocatable); the long-delay ends: {named}"
            + (f"; and {more} more" if more > 0 else "")
        )
    if problems:
        raise QueueMemoryRefused(
            "long-delay queue capacity estimate: " + "; ".join(problems) + ". The terminals' "
            "configured rates (their catalog files) and the installation setting "
            "network.queueMemory.serverShare (budget) change this result."
        )
