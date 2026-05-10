# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit tests for Scheduler Node Agent batch planning helpers."""

from __future__ import annotations

import pytest
from nodalarc.proto import node_agent_pb2
from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation
from scheduler.node_agent_batches import (
    build_link_down_batch_plan,
    build_link_up_batch_plan,
    successful_interface_acks,
)


class _Locator:
    def __init__(self, locality: int, node_ips: dict[str, str] | None = None) -> None:
        self._locality = locality
        self._node_ips = node_ips or {}

    def link_locality(self, node_a: str, node_b: str) -> int | None:
        return self._locality

    def agent_addr(self, node_id: str) -> str:
        return f"agent-{node_id}"

    def k3s_node(self, node_id: str) -> str:
        return f"k3s-{node_id}"

    def node_ip(self, k3s_node: str) -> str | None:
        return self._node_ips.get(k3s_node)


def _compensation(_node_a: str, _node_b: str, orbital_ms: float) -> LatencyCompensation:
    return LatencyCompensation(
        orbital_one_way_ms=orbital_ms,
        substrate_rtt_ms=2.0,
        substrate_one_way_ms=1.0,
        netem_one_way_ms=orbital_ms - 1.0,
        rtt_to_one_way_policy="half-rtt",
    )


def test_cross_node_isl_link_up_plan_builds_two_remote_interfaces():
    pair = ("sat-a", "sat-b")
    desired = {
        pair: ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=10.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=2997.9,
        )
    }
    locator = _Locator(
        node_agent_pb2.CROSS_NODE,
        node_ips={
            "k3s-sat-a": "10.0.0.1",
            "k3s-sat-b": "10.0.0.2",
        },
    )

    plan = build_link_up_batch_plan(
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
            latency_ms=10.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=2997.9,
        )
    }

    with pytest.raises(RuntimeError, match="missing IP"):
        build_link_up_batch_plan(
            pairs={pair},
            desired=desired,
            locator=_Locator(node_agent_pb2.CROSS_NODE, node_ips={"k3s-sat-a": "10.0.0.1"}),
            gs_capacities={},
            compensation_for_pair=_compensation,
        )


def test_local_ground_link_down_plan_preserves_single_agent_bridge_operation():
    pair = ("gs-den", "sat-a")
    actual = {
        pair: ActiveLinkInfo(
            interface_a="term0",
            interface_b="gnd0",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            link_type="ground",
            range_km=1500.0,
        )
    }

    plan = build_link_down_batch_plan(
        pairs={pair},
        actual_links=actual,
        locator=_Locator(node_agent_pb2.LOCAL),
        gs_capacities={"gs-den": 1},
    )

    assert set(plan.agent_ifaces) == {"agent-sat-a"}
    iface = plan.agent_ifaces["agent-sat-a"][0]
    assert iface.node_id == "gs-den"
    assert iface.interface_name == "term0"
    assert iface.peer_node_id == "sat-a"
    assert iface.peer_interface_name == "gnd0"
    assert iface.link_type == node_agent_pb2.GROUND


def test_successful_interface_acks_require_exact_identity_and_consistent_aggregate():
    requested = [
        node_agent_pb2.InterfaceUp(node_id="sat-a", interface_name="isl0"),
        node_agent_pb2.InterfaceUp(node_id="sat-b", interface_name="isl1"),
    ]
    ok = node_agent_pb2.BatchLinkUpResponse(
        success=True,
        interface_results=[
            node_agent_pb2.InterfaceResult(node_id="sat-a", interface_name="isl0", success=True),
            node_agent_pb2.InterfaceResult(node_id="sat-b", interface_name="isl1", success=True),
        ],
    )

    assert successful_interface_acks(
        result=ok,
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    ) == {
        ("agent-a", "sat-a", "isl0"),
        ("agent-a", "sat-b", "isl1"),
    }

    missing = node_agent_pb2.BatchLinkUpResponse(
        success=True,
        interface_results=[
            node_agent_pb2.InterfaceResult(node_id="sat-a", interface_name="isl0", success=True),
        ],
    )
    with pytest.raises(RuntimeError, match="did not identify every requested"):
        successful_interface_acks(
            result=missing,
            requested_interfaces=requested,
            agent_addr="agent-a",
            operation="BatchLinkUp",
        )

    inconsistent = node_agent_pb2.BatchLinkUpResponse(
        success=True,
        interface_results=[
            node_agent_pb2.InterfaceResult(node_id="sat-a", interface_name="isl0", success=True),
            node_agent_pb2.InterfaceResult(node_id="sat-b", interface_name="isl1", success=False),
        ],
    )
    with pytest.raises(RuntimeError, match="inconsistent aggregate success"):
        successful_interface_acks(
            result=inconsistent,
            requested_interfaces=requested,
            agent_addr="agent-a",
            operation="BatchLinkUp",
        )
