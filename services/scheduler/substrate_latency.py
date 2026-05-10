# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Substrate latency lookup contract for Scheduler dispatch.

The emulator can only subtract substrate latency that has been explicitly
measured or configured. Local links have zero substrate RTT by construction;
cross-node links without a measurement are unrepresentable and must fail
before Node Agent receives a partial topology.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class SubstrateLocator(Protocol):
    """Placement methods required for substrate RTT resolution."""

    def k3s_node(self, node_id: str) -> str | None: ...

    def node_ip(self, k3s_node: str) -> str | None: ...


def resolve_substrate_rtt_ms(
    *,
    locator: SubstrateLocator,
    live_rtt_by_peer_ip: Mapping[str, float],
    configured_rtt_by_node_pair: Mapping[str, float],
    node_a: str,
    node_b: str,
) -> float:
    """Resolve measured substrate RTT for a requested link.

    Live Node Agent measurements are keyed by the remote Kubernetes-node IP.
    Static operator measurements are keyed as ``node-a-node-b`` and are
    accepted in either direction. No cross-node zero fallback exists: if the
    substrate is unknown, the requested emulation cannot be proven.
    """
    k3s_a = locator.k3s_node(node_a)
    k3s_b = locator.k3s_node(node_b)
    if not k3s_a or not k3s_b:
        raise ValueError(
            f"Missing Kubernetes node placement for {node_a}<->{node_b}; "
            "refusing to treat unknown substrate locality as local"
        )
    if k3s_a == k3s_b:
        return 0.0

    ip_b = locator.node_ip(k3s_b)
    if ip_b and ip_b in live_rtt_by_peer_ip:
        return live_rtt_by_peer_ip[ip_b]

    forward_key = f"{k3s_a}-{k3s_b}"
    if forward_key in configured_rtt_by_node_pair:
        return configured_rtt_by_node_pair[forward_key]

    reverse_key = f"{k3s_b}-{k3s_a}"
    if reverse_key in configured_rtt_by_node_pair:
        return configured_rtt_by_node_pair[reverse_key]

    raise ValueError(
        f"No substrate RTT measurement for cross-node link {node_a}<->{node_b} "
        f"({k3s_a}<->{k3s_b}); refusing to dispatch with unknown substrate latency"
    )
