# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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

from nodalarc.models.resolved_session import InterfaceRates
from nodalarc.proto import node_agent_pb2
from nodalarc.vxlan import compute_vni

from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation, egress_ms

LinkPair = tuple[str, str]
InterfaceAck = tuple[str, str, str]  # agent_addr, node_id, interface_name


class LinkLocator(Protocol):
    """Placement methods needed to build Node Agent interface operations."""

    def link_locality(self, node_a: str, node_b: str) -> int: ...

    def agent_addr(self, node_id: str) -> str: ...

    def k3s_node(self, node_id: str) -> str: ...

    def node_ip(self, k3s_node: str) -> str: ...


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


def _ack_key(agent_addr: str, iface_msg) -> InterfaceAck:
    return (agent_addr, iface_msg.node_id, iface_msg.interface_name)


def link_type_of(info: ActiveLinkInfo) -> int:
    """The Node Agent mechanism a link runs on: terminal interfaces OME assigned for one pass,
    or interfaces the session fixed to each other."""
    return (
        node_agent_pb2.LINK_TYPE_ASSIGNED
        if info.assigned_terminals
        else node_agent_pb2.LINK_TYPE_FIXED
    )


def link_ends(pair: LinkPair, info: ActiveLinkInfo) -> tuple[tuple[str, str, str, str], ...]:
    """Each end of a link as (node, interface, peer node, peer interface), in pair order."""
    node_a, node_b = pair
    return (
        (node_a, info.interface_a, node_b, info.interface_b),
        (node_b, info.interface_b, node_a, info.interface_a),
    )


def wires_both_ends(info: ActiveLinkInfo, locality: int) -> bool:
    """Whether one Node Agent entry acts on both ends of a link: a local pass between two
    assigned terminals on one server. The one agent that hosts both pods joins the two host
    ports in one operation. Every other link is commanded end by end."""
    return (
        info.assigned_terminals
        and info.contact is None
        and locality == node_agent_pb2.LOCALITY_LOCAL
    )


def build_link_down_batch_plan(
    *,
    pairs: Iterable[LinkPair],
    actual_links: Mapping[LinkPair, ActiveLinkInfo],
    locator: LinkLocator,
    stage: int,
    ends: Mapping[LinkPair, frozenset[str]] | None = None,
    contact_ids: Mapping[tuple[str, str], str] | None = None,
) -> LinkDownBatchPlan:
    """Build per-agent BatchLinkDown operations.

    Every entry carries ``stage``. A local link only closes, all at once. A long-delay link
    first stops admission at both ends (STOP_ADMISSION), then closes each end on its own
    (CLOSE): ``ends`` names the ends of each pair the stage reaches, and each entry names the
    contact its end carries (``ActiveLinkInfo.end_contact_id``, or ``contact_ids`` by node id
    and interface where a restarted Scheduler found an end carrying another), so a stage cannot
    reach a newer contact on the same terminal.

    Missing actual links are ignored because there is nothing to remove.
    """
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}

    for pair in pairs:
        info = actual_links.get(pair)
        if info is None:
            continue

        node_a, node_b = pair
        locality = locator.link_locality(node_a, node_b)
        if info.contact is None and stage != node_agent_pb2.LINK_DOWN_STAGE_CLOSE:
            raise ValueError(f"local link {pair} closes at once; it has no admission stage")
        if info.contact is None and ends is not None and pair in ends:
            raise ValueError(f"local link {pair} closes both ends at once")
        if (
            info.contact is not None
            and stage == node_agent_pb2.LINK_DOWN_STAGE_CLOSE
            and (ends is None or pair not in ends)
        ):
            raise ValueError(
                f"long-delay link {pair} closes end by end; the close names no end of it"
            )

        cross = locality == node_agent_pb2.LOCALITY_CROSS_NODE
        vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b) if cross else 0
        closing = ends.get(pair) if ends is not None else None
        both_ends = wires_both_ends(info, locality)
        for nid, ifname, peer_nid, peer_ifname in link_ends(pair, info):
            if closing is not None and nid not in closing:
                continue
            agent = locator.agent_addr(nid)
            iface_msg = node_agent_pb2.InterfaceDown(
                node_id=nid,
                interface_name=ifname,
                link_type=link_type_of(info),
                locality=locality,
                vni=vni,
                remote_node_ip=locator.node_ip(locator.k3s_node(peer_nid)) if cross else "",
                peer_node_id=peer_nid,
                peer_interface_name=peer_ifname,
                stage=stage,
                contact_id=(
                    ""
                    if info.contact is None
                    else (contact_ids or {}).get((nid, ifname)) or info.end_contact_id(nid)
                ),
            )
            agent_ifaces.setdefault(agent, []).append(iface_msg)
            pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))
            if both_ends:
                # The entry of the pair's first node closes both ends.
                break

    return LinkDownBatchPlan(
        agent_ifaces=agent_ifaces,
        pair_agent_ifaces=pair_agent_ifaces,
    )


def interface_terminal_rates(
    interface_rates: Mapping[tuple[str, str], InterfaceRates], node_id: str, interface: str
) -> InterfaceRates:
    """One WAN interface's own terminal rates; absence is a loud failure."""
    rates = interface_rates.get((node_id, interface))
    if rates is None:
        raise RuntimeError(
            f"no terminal rates for {node_id}/{interface}: the resolved session "
            "names no terminal behind this interface"
        )
    return rates


def terminal_rates_message(
    interface_rates: Mapping[tuple[str, str], InterfaceRates], node_id: str, interface: str
) -> node_agent_pb2.TerminalRates:
    """The command form of one WAN interface's own terminal rates."""
    rates = interface_terminal_rates(interface_rates, node_id, interface)
    return node_agent_pb2.TerminalRates(
        transmit_mbps=rates.transmit_mbps, receive_mbps=rates.receive_mbps
    )


def build_link_up_batch_plan(
    *,
    pairs: Iterable[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    locator: LinkLocator,
    compensation_for_pair: Callable[[str, str, tuple[float, float]], LatencyCompensation],
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    stage: int,
) -> LinkUpBatchPlan:
    """Build per-agent BatchLinkUp operations and per-pair latency provenance.

    Every entry carries the terminal rates of the interfaces it wires, so the
    Node Agent shapes each end as its own terminal, and ``stage``. A local link
    opens at once (OPEN). A long-delay contact is built first at both ends with
    admission stopped (PREPARE) and admitted after both ends proved it (OPEN,
    from ``restage_link_up_plan`` so both stages carry the same delays).
    """
    if stage not in (node_agent_pb2.LINK_UP_STAGE_PREPARE, node_agent_pb2.LINK_UP_STAGE_OPEN):
        raise ValueError(f"a LinkUp is built for PREPARE or OPEN, not stage {stage}")
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    pair_compensation: dict[LinkPair, LatencyCompensation] = {}

    for pair in pairs:
        info = desired.get(pair)
        if info is None:
            raise RuntimeError(f"Dispatch planner requested LinkUp for missing desired pair {pair}")
        if info.contact is None and stage != node_agent_pb2.LINK_UP_STAGE_OPEN:
            raise ValueError(f"{pair} is a local link; it opens at once and has no PREPARE")

        node_a, node_b = pair
        locality = locator.link_locality(node_a, node_b)

        compensation = compensation_for_pair(node_a, node_b, info.latencies_ms)
        pair_compensation[pair] = compensation
        # Each end's egress carries the direction it transmits.
        netem = (compensation.netem_a_to_b_ms, compensation.netem_b_to_a_ms)
        # Remember what was COMMANDED: kernel proofs assert against these values.
        # Recomputing compensation at proof time reads live substrate RTT and
        # reports normal measurement drift as kernel divergence.
        info.commanded_netem_ms = netem

        cross = locality == node_agent_pb2.LOCALITY_CROSS_NODE
        vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b) if cross else 0
        both_ends = wires_both_ends(info, locality)
        for nid, ifname, peer_nid, peer_ifname in link_ends(pair, info):
            agent = locator.agent_addr(nid)
            iface_msg = node_agent_pb2.InterfaceUp(
                node_id=nid,
                interface_name=ifname,
                link_type=link_type_of(info),
                latency_ms=egress_ms(pair, nid, netem),
                rates=terminal_rates_message(interface_rates, nid, ifname),
                locality=locality,
                remote_node_ip=locator.node_ip(locator.k3s_node(peer_nid)) if cross else "",
                vni=vni,
                peer_node_id=peer_nid,
                peer_interface_name=peer_ifname,
                stage=stage,
            )
            if info.contact is not None:
                # A long-delay end, fixed or assigned, stages on its own in both localities. It
                # holds the delay queue of the direction it receives: the one its peer
                # transmits. The peer's transmit rate sizes that queue.
                iface_msg.contact_id = info.contact.contact_id
                iface_msg.receive_latency_ms = egress_ms(pair, peer_nid, netem)
                iface_msg.peer_rates.CopyFrom(
                    terminal_rates_message(interface_rates, peer_nid, peer_ifname)
                )
            if both_ends:
                # One entry wires both ends: it carries the peer end's delay and rates too.
                iface_msg.peer_latency_ms = egress_ms(pair, peer_nid, netem)
                iface_msg.peer_rates.CopyFrom(
                    terminal_rates_message(interface_rates, peer_nid, peer_ifname)
                )
            agent_ifaces.setdefault(agent, []).append(iface_msg)
            pair_agent_ifaces.setdefault(pair, set()).add(_ack_key(agent, iface_msg))
            if both_ends:
                break

    return LinkUpBatchPlan(
        agent_ifaces=agent_ifaces,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_compensation=pair_compensation,
    )


def restage_link_up_plan(
    plan: LinkUpBatchPlan, *, pairs: Iterable[LinkPair], stage: int
) -> LinkUpBatchPlan:
    """The entries of ``pairs`` in ``plan`` with their stage replaced: the OPEN of contacts both
    of whose ends PREPARE proved. Every other field is the prepared one, so OPEN verifies the
    queue PREPARE built with the delays it was built with. Rejected alternative: building OPEN
    anew, which recomputes latency compensation from a substrate measurement that may have moved
    since PREPARE; an OPEN whose delay differs from its queue's is refused."""
    chosen = {pair: plan.pair_agent_ifaces[pair] for pair in pairs}
    acks: set[InterfaceAck] = set().union(*chosen.values()) if chosen else set()
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]] = {}
    for agent, entries in plan.agent_ifaces.items():
        for entry in entries:
            if _ack_key(agent, entry) not in acks:
                continue
            restaged = node_agent_pb2.InterfaceUp()
            restaged.CopyFrom(entry)
            restaged.stage = stage
            agent_ifaces.setdefault(agent, []).append(restaged)
    return LinkUpBatchPlan(
        agent_ifaces=agent_ifaces,
        pair_agent_ifaces=chosen,
        pair_compensation={pair: plan.pair_compensation[pair] for pair in chosen},
    )
