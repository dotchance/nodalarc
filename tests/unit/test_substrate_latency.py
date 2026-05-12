# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Scheduler substrate RTT resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from scheduler.substrate_latency import (
    LiveSubstrateMeasurement,
    parse_substrate_measurement_event,
    resolve_substrate_rtt_ms,
)

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Locator:
    def __init__(self) -> None:
        self.nodes: dict[str, str | None] = {}
        self.ips: dict[str, str | None] = {}

    def k3s_node(self, node_id: str) -> str | None:
        return self.nodes.get(node_id)

    def node_ip(self, k3s_node: str) -> str | None:
        return self.ips.get(k3s_node)


def _measurement(
    remote_ip: str,
    rtt_ms: float | None = 4.0,
    *,
    status: str = "ok",
    generation: str = WIRING_GENERATION,
    stale_after: datetime | None = None,
) -> LiveSubstrateMeasurement:
    return LiveSubstrateMeasurement(
        remote_ip=remote_ip,
        session_id=SESSION_ID,
        wiring_generation=generation,
        source_node="node-a",
        measured_at=NOW,
        stale_after=stale_after or NOW + timedelta(seconds=60),
        status=status,
        sample_count=10,
        success_count=10 if status == "ok" else 0,
        median_rtt_ms=rtt_ms,
        min_rtt_ms=rtt_ms,
        max_rtt_ms=rtt_ms,
        error_message="" if status == "ok" else "ping failed",
    )


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
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
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
            live_rtt_by_peer_ip={"10.0.0.2": _measurement("10.0.0.2", 4.0)},
            configured_rtt_by_node_pair={"node-a-node-b": 9.0},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
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
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
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
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
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
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_stale_live_measurement_blocks_static_fallback() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc.ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="stale"):
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={
                "10.0.0.2": _measurement(
                    "10.0.0.2",
                    stale_after=NOW - timedelta(seconds=1),
                )
            },
            configured_rtt_by_node_pair={"node-a-node-b": 9.0},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_generation_mismatched_live_measurement_blocks_dispatch() -> None:
    loc = _Locator()
    loc.nodes.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc.ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="generation mismatch"):
        resolve_substrate_rtt_ms(
            locator=loc,
            live_rtt_by_peer_ip={
                "10.0.0.2": _measurement(
                    "10.0.0.2",
                    generation="sha256:" + "b" * 64,
                )
            },
            configured_rtt_by_node_pair={},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_parse_substrate_event_requires_structured_generation_scoped_measurements() -> None:
    event = {
        "source_node": "node-a",
        "session_id": SESSION_ID,
        "wiring_generation": WIRING_GENERATION,
        "timestamp": NOW.isoformat(),
        "measurements": {
            "10.0.0.2": {
                "timestamp": NOW.isoformat(),
                "stale_after": (NOW + timedelta(seconds=60)).isoformat(),
                "status": "ok",
                "sample_count": 10,
                "success_count": 10,
                "median_rtt_ms": 4.0,
                "min_rtt_ms": 3.5,
                "max_rtt_ms": 4.5,
                "refs": [
                    {
                        "session_id": SESSION_ID,
                        "wiring_generation": WIRING_GENERATION,
                        "remote_ip": "10.0.0.2",
                        "vni": 1001,
                        "local_ifname": "isl0",
                    }
                ],
            }
        },
    }

    parsed = parse_substrate_measurement_event(event)

    assert parsed["10.0.0.2"].median_rtt_ms == 4.0
    assert parsed["10.0.0.2"].wiring_generation == WIRING_GENERATION


def test_parse_substrate_event_rejects_legacy_peer_map() -> None:
    with pytest.raises(ValueError, match="measurements map"):
        parse_substrate_measurement_event(
            {
                "source_node": "node-a",
                "peers": {"10.0.0.2": 4.0},
                "timestamp": NOW.isoformat(),
            }
        )
