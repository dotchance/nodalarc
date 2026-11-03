# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for Scheduler Node Agent batch planning helpers."""

from __future__ import annotations

import pytest
from nodalarc.models.resolved_session import InterfaceRates
from nodalarc.proto import node_agent_pb2
from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation, compensate_latency
from scheduler.node_agent_batches import build_link_down_batch_plan, build_link_up_batch_plan
from scheduler.pod_locator import PodLocationError, PodLocationMap

from tests.terminal_rate_fixtures import ANY_INTERFACE_RATES


class _Locator:
    def __init__(self, locality: int, node_ips: dict[str, str] | None = None) -> None:
        self._locality = locality
        self._node_ips = node_ips or {}

    def link_locality(self, node_a: str, node_b: str) -> int:
        return self._locality

    def agent_addr(self, node_id: str) -> str:
        return f"agent-{node_id}"

    def k3s_node(self, node_id: str) -> str:
        return f"k3s-{node_id}"

    def node_ip(self, k3s_node: str) -> str:
        if k3s_node not in self._node_ips:
            raise PodLocationError(f"no InternalIP for Kubernetes node {k3s_node}")
        return self._node_ips[k3s_node]


def _compensation(
    _node_a: str, _node_b: str, orbital_ms: tuple[float, float]
) -> LatencyCompensation:
    """The Scheduler's compensation over a measured 2 ms substrate RTT: each direction's netem
    delay is its orbital delay less 1 ms."""
    return compensate_latency(
        orbital_a_to_b_ms=orbital_ms[0], orbital_b_to_a_ms=orbital_ms[1], substrate_rtt_ms=2.0
    )


def test_cross_node_isl_link_up_plan_builds_two_remote_interfaces():
    pair = ("sat-a", "sat-b")
    desired = {
        pair: ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_a_to_b_ms=10.0,
            latency_b_to_a_ms=10.0,
            link_type="isl",
            range_km=2997.9,
        )
    }
    locator = _Locator(
        node_agent_pb2.LOCALITY_CROSS_NODE,
        node_ips={
            "k3s-sat-a": "10.0.0.1",
            "k3s-sat-b": "10.0.0.2",
        },
    )

    # Each ISL end is its own terminal: sat-b transmits slower than sat-a receives.
    interface_rates = {
        ("sat-a", "isl0"): InterfaceRates(transmit_mbps=2000.0, receive_mbps=1500.0),
        ("sat-b", "isl1"): InterfaceRates(transmit_mbps=100.0, receive_mbps=2000.0),
    }

    plan = build_link_up_batch_plan(
        interface_rates=interface_rates,
        pairs={pair},
        desired=desired,
        locator=locator,
        gs_capacities={},
        compensation_for_pair=_compensation,
    )

    assert set(plan.agent_ifaces) == {"agent-sat-a", "agent-sat-b"}
    ifaces = [iface for batch in plan.agent_ifaces.values() for iface in batch]
    assert {iface.node_id for iface in ifaces} == {"sat-a", "sat-b"}
    assert {iface.remote_node_ip for iface in ifaces} == {"10.0.0.1", "10.0.0.2"}
    assert {iface.latency_ms for iface in ifaces} == {9.0}
    assert {
        (iface.node_id, iface.rates.transmit_mbps, iface.rates.receive_mbps) for iface in ifaces
    } == {("sat-a", 2000.0, 1500.0), ("sat-b", 100.0, 2000.0)}
    assert not any(iface.HasField("peer_rates") for iface in ifaces)
    assert plan.pair_agent_ifaces[pair] == {
        ("agent-sat-a", "sat-a", "isl0"),
        ("agent-sat-b", "sat-b", "isl1"),
    }


def test_cross_node_link_up_missing_remote_ip_fails_loudly():
    pair = ("sat-a", "sat-b")
    desired = {
        pair: ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_a_to_b_ms=10.0,
            latency_b_to_a_ms=10.0,
            link_type="isl",
            range_km=2997.9,
        )
    }

    # The real locator: sat-b's hosting node has no InternalIP.
    locator = PodLocationMap()
    locator._node_of.update({"sat-a": "k3s-sat-a", "sat-b": "k3s-sat-b"})
    locator._agent_addrs.update({"k3s-sat-a": "agent-a", "k3s-sat-b": "agent-b"})
    locator._node_ips["k3s-sat-a"] = "10.0.0.1"

    with pytest.raises(PodLocationError, match="no InternalIP for Kubernetes node k3s-sat-b"):
        build_link_up_batch_plan(
            interface_rates=ANY_INTERFACE_RATES,
            pairs={pair},
            desired=desired,
            locator=locator,
            gs_capacities={},
            compensation_for_pair=_compensation,
        )


def test_local_ground_link_down_plan_preserves_single_agent_bridge_operation():
    pair = ("gs-den", "sat-a")
    actual = {
        pair: ActiveLinkInfo(
            interface_a="term0",
            interface_b="gnd0",
            latency_a_to_b_ms=5.0,
            latency_b_to_a_ms=5.0,
            link_type="ground",
            range_km=1500.0,
        )
    }

    plan = build_link_down_batch_plan(
        pairs={pair},
        actual_links=actual,
        locator=_Locator(node_agent_pb2.LOCALITY_LOCAL),
        gs_capacities={"gs-den": 1},
    )

    assert set(plan.agent_ifaces) == {"agent-sat-a"}
    iface = plan.agent_ifaces["agent-sat-a"][0]
    assert iface.node_id == "gs-den"
    assert iface.interface_name == "term0"
    assert iface.peer_node_id == "sat-a"
    assert iface.peer_interface_name == "gnd0"
    assert iface.link_type == node_agent_pb2.LINK_TYPE_GROUND


def test_link_up_without_terminal_rates_for_an_interface_fails_loudly():
    pair = ("sat-a", "sat-b")
    desired = {
        pair: ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_a_to_b_ms=10.0,
            latency_b_to_a_ms=10.0,
            link_type="isl",
            range_km=2997.9,
        )
    }

    with pytest.raises(RuntimeError, match="no terminal rates for sat-b/isl1"):
        build_link_up_batch_plan(
            interface_rates={
                ("sat-a", "isl0"): InterfaceRates(transmit_mbps=2000.0, receive_mbps=2000.0)
            },
            pairs={pair},
            desired=desired,
            locator=_Locator(
                node_agent_pb2.LOCALITY_CROSS_NODE,
                node_ips={"k3s-sat-a": "10.0.0.1", "k3s-sat-b": "10.0.0.2"},
            ),
            gs_capacities={},
            compensation_for_pair=_compensation,
        )


def _messages(plan) -> list[node_agent_pb2.InterfaceUp]:
    return [iface for batch in plan.agent_ifaces.values() for iface in batch]


@pytest.mark.parametrize(
    "locality",
    [node_agent_pb2.LOCALITY_LOCAL, node_agent_pb2.LOCALITY_CROSS_NODE],
    ids=["local", "cross-node"],
)
def test_every_end_carries_the_delay_of_the_direction_it_transmits(locality):
    """Every link kind the plan builds: an ISL, a ground link with the station first and one
    with the satellite first. pair[0] transmits a_to_b and pair[1] transmits b_to_a; each end's
    netem delay is that direction's delay less the 1 ms substrate share. Every message carries
    the delay of the end it names (and, on a LOCAL ground message, of its peer); every end is
    described; every delay in the plan is distinct, so a swap between the ends of a link or
    between links fails."""
    desired = {
        ("sat-a", "sat-b"): ActiveLinkInfo(
            "isl0", "isl1", 10.0, 12.0, link_type="isl", range_km=3000.0
        ),
        ("gs-den", "sat-c"): ActiveLinkInfo(
            "term0", "gnd0", 5.0, 7.0, link_type="ground", range_km=1500.0
        ),
        ("earth-sat-d", "gs-hou"): ActiveLinkInfo(
            "gnd0", "term0", 6.0, 8.0, link_type="ground", range_km=1800.0
        ),
    }
    transmits: dict[str, float] = {}
    interface_rates: dict[tuple[str, str], InterfaceRates] = {}
    for (node_a, node_b), info in desired.items():
        transmits[node_a] = info.latency_a_to_b_ms - 1.0
        transmits[node_b] = info.latency_b_to_a_ms - 1.0
        for node, interface in ((node_a, info.interface_a), (node_b, info.interface_b)):
            interface_rates[(node, interface)] = InterfaceRates(
                transmit_mbps=1000.0, receive_mbps=1000.0
            )
    assert len(set(transmits.values())) == len(transmits)

    plan = build_link_up_batch_plan(
        interface_rates=interface_rates,
        pairs=set(desired),
        desired=desired,
        locator=_Locator(
            locality,
            node_ips={f"k3s-{node}": f"10.0.0.{index}" for index, node in enumerate(transmits)},
        ),
        gs_capacities={"gs-den": 1, "gs-hou": 1},
        compensation_for_pair=_compensation,
    )

    described: set[str] = set()
    for message in _messages(plan):
        assert message.latency_ms == transmits[message.node_id], message
        described.add(message.node_id)
        if (
            message.link_type == node_agent_pb2.LINK_TYPE_GROUND
            and message.locality == node_agent_pb2.LOCALITY_LOCAL
        ):
            assert message.peer_latency_ms == transmits[message.peer_node_id], message
            described.add(message.peer_node_id)
        else:
            assert message.peer_latency_ms == 0.0, message
    assert described == set(transmits)
    for (node_a, node_b), info in desired.items():
        assert info.commanded_netem_ms == (transmits[node_a], transmits[node_b])
