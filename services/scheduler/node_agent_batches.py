# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent batch planning helpers for Scheduler actuation.

The Dispatcher owns ordering, state mutation, and event publication. This
module owns the deterministic translation from desired/actual links into
Node Agent protobuf interface operations, plus exact per-interface ACK
validation. That boundary makes protocol construction testable without a NATS
connection or a running dispatch worker.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from nodalarc.proto import node_agent_pb2
from nodalarc.vxlan import compute_vni

from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation

LinkPair = tuple[str, str]
InterfaceAck = tuple[str, str, str]  # agent_addr, node_id, interface_name


class LinkLocator(Protocol):
    """Placement methods needed to build Node Agent interface operations."""

    def link_locality(self, node_a: str, node_b: str) -> int | None: ...

    def agent_addr(self, node_id: str) -> str: ...

    def k3s_node(self, node_id: str) -> str: ...

    def node_ip(self, k3s_node: str) -> str | None: ...


@dataclass(frozen=True)
class LinkUpBatchPlan:
    """Per-agent LinkUp operations plus proof data for pair-level success."""

    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]]
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]]
    pair_compensation: dict[LinkPair, LatencyCompensation]


@dataclass(frozen=True)
class LinkDownBatchPlan:
    """Per-agent LinkDown operations plus proof data for pair-level success."""

    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]]
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]]
    skipped_unscheduled: frozenset[LinkPair]


def _ack_key(agent_addr: str, iface_msg) -> InterfaceAck:
    return (agent_addr, iface_msg.node_id, iface_msg.interface_name)


def _ground_ids(
    pair: LinkPair,
    gs_capacities: Mapping[str, int],
) -> tuple[str, str]:
    node_a, node_b = pair
    gs_id = node_a if node_a in gs_capacities else node_b
    sat_id = node_b if node_a in gs_capacities else node_a
    return gs_id, sat_id


def _ground_ifaces(
    pair: LinkPair,
    info: ActiveLinkInfo,
    gs_capacities: Mapping[str, int],
) -> tuple[str, str]:
    node_a, _node_b = pair
    gs_iface = info.interface_a if node_a in gs_capacities else info.interface_b
    sat_iface = info.interface_b if node_a in gs_capacities else info.interface_a
    return gs_iface, sat_iface


def build_link_down_batch_plan(
    *,
    pairs: Iterable[LinkPair],
    actual_links: Mapping[LinkPair, ActiveLinkInfo],
    locator: LinkLocator,
    gs_capacities: Mapping[str, int],
) -> LinkDownBatchPlan:
    """Build per-agent BatchLinkDown operations.

    Missing actual links are ignored because there is nothing to remove.
    Unknown placement is reported to the caller as skipped rather than silently
    represented as success.
    """
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    skipped_unscheduled: set[LinkPair] = set()

    for pair in pairs:
        info = actual_links.get(pair)
        if info is None:
            continue

        node_a, node_b = pair
        locality = locator.link_locality(node_a, node_b)
        if locality is None:
            skipped_unscheduled.add(pair)
            continue

        if info.link_type == "ground":
            gs_id, sat_id = _ground_ids(pair, gs_capacities)
            gs_iface, sat_iface = _ground_ifaces(pair, info, gs_capacities)
            vni = (
                compute_vni(gs_id, sat_id, gs_iface, sat_iface)
                if locality == node_agent_pb2.CROSS_NODE
                else 0
            )

            if locality == node_agent_pb2.LOCAL:
                agent = locator.agent_addr(sat_id)
                iface_msg = node_agent_pb2.InterfaceDown(
                    node_id=gs_id,
                    interface_name=gs_iface,
                    peer_node_id=sat_id,
                    peer_interface_name=sat_iface,
                    link_type=node_agent_pb2.GROUND,
                    gs_id=gs_id,
                    sat_id=sat_id,
                    locality=locality,
                    remote_node_ip="",
                    vni=vni,
                )
                agent_ifaces.setdefault(agent, []).append(iface_msg)
                pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))
            else:
                for nid, agent_addr in [
                    (sat_id, locator.agent_addr(sat_id)),
                    (gs_id, locator.agent_addr(gs_id)),
                ]:
                    iface = gs_iface if nid == gs_id else sat_iface
                    peer_nid = sat_id if nid == gs_id else gs_id
                    peer_iface = sat_iface if nid == gs_id else gs_iface
                    iface_msg = node_agent_pb2.InterfaceDown(
                        node_id=nid,
                        interface_name=iface,
                        peer_node_id=peer_nid,
                        peer_interface_name=peer_iface,
                        link_type=node_agent_pb2.GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        locality=locality,
                        remote_node_ip="",
                        vni=vni,
                    )
                    agent_ifaces.setdefault(agent_addr, []).append(iface_msg)
                    pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent_addr, iface_msg))
            continue

        vni = (
            compute_vni(node_a, node_b, info.interface_a, info.interface_b)
            if locality == node_agent_pb2.CROSS_NODE
            else 0
        )
        for nid, ifname, peer_nid, peer_ifname in [
            (node_a, info.interface_a, node_b, info.interface_b),
            (node_b, info.interface_b, node_a, info.interface_a),
        ]:
            agent = locator.agent_addr(nid)
            iface_msg = node_agent_pb2.InterfaceDown(
                node_id=nid,
                interface_name=ifname,
                link_type=node_agent_pb2.ISL,
                locality=locality,
                vni=vni,
                peer_node_id=peer_nid,
                peer_interface_name=peer_ifname,
            )
            agent_ifaces.setdefault(agent, []).append(iface_msg)
            pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))

    return LinkDownBatchPlan(
        agent_ifaces=agent_ifaces,
        pair_agent_ifaces=pair_agent_ifaces,
        skipped_unscheduled=frozenset(skipped_unscheduled),
    )


def build_link_up_batch_plan(
    *,
    pairs: Iterable[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    locator: LinkLocator,
    gs_capacities: Mapping[str, int],
    compensation_for_pair: Callable[[str, str, float], LatencyCompensation],
) -> LinkUpBatchPlan:
    """Build per-agent BatchLinkUp operations and per-pair latency provenance."""
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    pair_compensation: dict[LinkPair, LatencyCompensation] = {}

    for pair in pairs:
        info = desired.get(pair)
        if info is None:
            raise RuntimeError(f"Dispatch planner requested LinkUp for missing desired pair {pair}")

        node_a, node_b = pair
        locality = locator.link_locality(node_a, node_b)
        if locality is None:
            raise RuntimeError(
                f"Cannot dispatch LinkUp for {node_a}<->{node_b}: pod placement is unknown"
            )

        compensation = compensation_for_pair(node_a, node_b, info.latency_ms)
        pair_compensation[pair] = compensation
        netem_ms = compensation.netem_one_way_ms

        if info.link_type == "ground":
            gs_id, sat_id = _ground_ids(pair, gs_capacities)
            gs_iface, sat_iface = _ground_ifaces(pair, info, gs_capacities)
            vni = (
                compute_vni(gs_id, sat_id, gs_iface, sat_iface)
                if locality == node_agent_pb2.CROSS_NODE
                else 0
            )

            if locality == node_agent_pb2.LOCAL:
                agent = locator.agent_addr(sat_id)
                iface_msg = node_agent_pb2.InterfaceUp(
                    node_id=gs_id,
                    interface_name=gs_iface,
                    peer_node_id=sat_id,
                    peer_interface_name=sat_iface,
                    link_type=node_agent_pb2.GROUND,
                    latency_ms=netem_ms,
                    bandwidth_mbps=info.bandwidth_mbps,
                    gs_id=gs_id,
                    sat_id=sat_id,
                    locality=locality,
                    remote_node_ip="",
                    vni=vni,
                )
                agent_ifaces.setdefault(agent, []).append(iface_msg)
                pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))
            else:
                for nid, peer_nid in [(sat_id, gs_id), (gs_id, sat_id)]:
                    peer_k3s = locator.k3s_node(peer_nid)
                    remote_ip = locator.node_ip(peer_k3s)
                    if not remote_ip:
                        raise RuntimeError(
                            f"CROSS_NODE GS LinkUp {gs_id}<->{sat_id}: "
                            f"missing IP for Kubernetes node {peer_k3s}"
                        )
                    iface = gs_iface if nid == gs_id else sat_iface
                    peer_iface = sat_iface if nid == gs_id else gs_iface
                    agent_addr = locator.agent_addr(nid)
                    iface_msg = node_agent_pb2.InterfaceUp(
                        node_id=nid,
                        interface_name=iface,
                        peer_node_id=peer_nid,
                        peer_interface_name=peer_iface,
                        link_type=node_agent_pb2.GROUND,
                        latency_ms=netem_ms,
                        bandwidth_mbps=info.bandwidth_mbps,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        locality=locality,
                        remote_node_ip=remote_ip,
                        vni=vni,
                    )
                    agent_ifaces.setdefault(agent_addr, []).append(iface_msg)
                    pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent_addr, iface_msg))
            continue

        vni = (
            compute_vni(node_a, node_b, info.interface_a, info.interface_b)
            if locality == node_agent_pb2.CROSS_NODE
            else 0
        )
        for nid, ifname, peer_nid, peer_ifname in [
            (node_a, info.interface_a, node_b, info.interface_b),
            (node_b, info.interface_b, node_a, info.interface_a),
        ]:
            agent = locator.agent_addr(nid)
            remote_ip = ""
            if locality == node_agent_pb2.CROSS_NODE:
                peer_k3s = locator.k3s_node(peer_nid)
                remote_ip = locator.node_ip(peer_k3s)
                if not remote_ip:
                    raise RuntimeError(
                        f"CROSS_NODE ISL LinkUp {node_a}<->{node_b}: "
                        f"missing IP for Kubernetes node {peer_k3s}"
                    )
            iface_msg = node_agent_pb2.InterfaceUp(
                node_id=nid,
                interface_name=ifname,
                link_type=node_agent_pb2.ISL,
                latency_ms=netem_ms,
                bandwidth_mbps=info.bandwidth_mbps,
                locality=locality,
                remote_node_ip=remote_ip,
                vni=vni,
                peer_node_id=peer_nid,
                peer_interface_name=peer_ifname,
            )
            agent_ifaces.setdefault(agent, []).append(iface_msg)
            pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))

    return LinkUpBatchPlan(
        agent_ifaces=agent_ifaces,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_compensation=pair_compensation,
    )


def successful_interface_acks(
    *,
    result,
    requested_interfaces,
    agent_addr: str,
    operation: str,
) -> set[InterfaceAck]:
    """Return exact successful (agent, node, interface) ACKs.

    Aggregate counts are not proof. Every requested interface must be named in
    the response, and aggregate success must agree with per-interface success.
    """
    requested = {(iface.node_id, iface.interface_name) for iface in requested_interfaces}
    returned = {(ack.node_id, ack.interface_name) for ack in result.interface_results}
    if requested != returned:
        raise RuntimeError(
            f"{operation} response from {agent_addr} did not identify every requested "
            f"interface: requested={sorted(requested)} returned={sorted(returned)}"
        )

    all_interface_success = all(ack.success for ack in result.interface_results)
    if bool(result.success) != all_interface_success:
        raise RuntimeError(
            f"{operation} response from {agent_addr} has inconsistent aggregate success: "
            f"success={result.success} per_interface_success={all_interface_success}"
        )

    return {
        (agent_addr, ack.node_id, ack.interface_name)
        for ack in result.interface_results
        if ack.success
    }
