# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Scheduler substrate RTT resolution."""

from __future__ import annotations

import pytest
from scheduler.substrate_latency import resolve_substrate_rtt_ms


class _Locator:
    def __init__(self) -> None:
        self.nodes: dict[str, str | None] = {}
        self.ips: dict[str, str | None] = {}

    def k3s_node(self, node_id: str) -> str | None:
        return self.nodes.get(node_id)

    def node_ip(self, k3s_node: str) -> str | None:
        return self.ips.get(k3s_node)


def test_local_links_have_zero_substrate_rtt() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-1", "sat-b": "node-1"})

    assert (
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={},
            configured_rtt_by_node_pair={},
            node_a="sat-a",
            node_b="sat-b",
        )
        == 0.0
    )


def test_cross_node_prefers_live_peer_ip_measurement() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc.ips["node-b"] = "10.0.0.2"

    assert (
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={"10.0.0.2": 4.0},
            configured_rtt_by_node_pair={"node-a-node-b": 9.0},
            node_a="sat-a",
            node_b="sat-b",
        )
        == 4.0
    )


def test_cross_node_accepts_explicit_reverse_configured_measurement() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-a", "sat-b": "node-b"})

    assert (
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={},
            configured_rtt_by_node_pair={"node-b-node-a": 7.0},
            node_a="sat-a",
            node_b="sat-b",
        )
        == 7.0
    )


def test_cross_node_missing_measurement_fails_loudly() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-a", "sat-b": "node-b"})

    with pytest.raises(ValueError, match="No substrate RTT measurement"):
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={},
            configured_rtt_by_node_pair={},
            node_a="sat-a",
            node_b="sat-b",
        )


def test_missing_placement_is_not_treated_as_local() -> None:
    loc = _Locator()
    loc.nodes["sat-a"] = "node-a"

    with pytest.raises(ValueError, match="Missing Kubernetes node placement"):
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={},
            configured_rtt_by_node_pair={},
            node_a="sat-a",
            node_b="sat-b",
        )
