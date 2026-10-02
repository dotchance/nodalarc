# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for Scheduler substrate RTT resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.substrate.measurement_contract import (
    RequiredSubstratePair,
    SubstrateMeasurement,
    SubstrateStatusDocument,
)
from scheduler.pod_locator import PodLocationError, PodLocationMap
from scheduler.substrate_latency import (
    resolve_substrate_rtt_ms,
    validate_required_substrate_measurements,
)

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _locator() -> PodLocationMap:
    """A real PodLocationMap; each test places the nodes it needs."""
    return PodLocationMap()


def _measurement(
    rtt_ms: float | None = 4.0,
    *,
    status: str = "ok",
    generation: str = WIRING_GENERATION,
    stale_after: datetime | None = None,
) -> SubstrateMeasurement:
    return SubstrateMeasurement(
        session_id=SESSION_ID,
        wiring_generation=generation,
        source_node="node-a",
        source_ip="10.0.0.1",
        target_node="node-b",
        target_ip="10.0.0.2",
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


def _required_pair() -> RequiredSubstratePair:
    return RequiredSubstratePair.build(
        source_node="node-a",
        source_ip="10.0.0.1",
        target_node="node-b",
        target_ip="10.0.0.2",
        reasons=["isl"],
    )


def test_local_links_have_zero_substrate_rtt() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-1", "sat-b": "node-1"})

    assert (
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )
        == 0.0
    )


def test_cross_node_uses_directional_substrate_measurement() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc._node_ips["node-a"] = "10.0.0.1"
    loc._node_ips["node-b"] = "10.0.0.2"

    assert (
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={"node-a->node-b": _measurement(4.0)},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )
        == 4.0
    )


def test_cross_node_does_not_accept_reverse_direction_as_proof() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc._node_ips["node-a"] = "10.0.0.1"
    loc._node_ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="No substrate RTT measurement"):
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={"node-b->node-a": _measurement(4.0)},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_cross_node_missing_measurement_fails_loudly() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc._node_ips["node-a"] = "10.0.0.1"
    loc._node_ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="No substrate RTT measurement"):
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_missing_placement_is_not_treated_as_local() -> None:
    loc = _locator()
    loc._node_of["sat-a"] = "node-a"

    with pytest.raises(PodLocationError, match="no pod location for node sat-b"):
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={},
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_stale_measurement_blocks_dispatch() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc._node_ips["node-a"] = "10.0.0.1"
    loc._node_ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="stale"):
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={
                "node-a->node-b": _measurement(stale_after=NOW - timedelta(seconds=1))
            },
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_generation_mismatched_measurement_blocks_dispatch() -> None:
    loc = _locator()
    loc._node_of.update({"sat-a": "node-a", "sat-b": "node-b"})
    loc._node_ips["node-a"] = "10.0.0.1"
    loc._node_ips["node-b"] = "10.0.0.2"

    with pytest.raises(ValueError, match="identity mismatch"):
        resolve_substrate_rtt_ms(
            locator=loc,
            measurements_by_direction={
                "node-a->node-b": _measurement(generation="sha256:" + "b" * 64)
            },
            node_a="sat-a",
            node_b="sat-b",
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_validate_required_measurements_indexes_complete_status() -> None:
    indexed = validate_required_substrate_measurements(
        required_pairs=[_required_pair()],
        documents_by_source={
            "node-a": SubstrateStatusDocument(
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
                source_node="node-a",
                measurements={"node-b": _measurement()},
            )
        },
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        now=NOW,
    )

    assert indexed["node-a->node-b"].median_rtt_ms == 4.0


def test_validate_required_measurements_rejects_missing_document() -> None:
    with pytest.raises(ValueError, match="missing source status document"):
        validate_required_substrate_measurements(
            required_pairs=[_required_pair()],
            documents_by_source={},
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )


def test_validate_required_measurements_rejects_failed_status() -> None:
    with pytest.raises(ValueError, match="failed"):
        validate_required_substrate_measurements(
            required_pairs=[_required_pair()],
            documents_by_source={
                "node-a": SubstrateStatusDocument(
                    session_id=SESSION_ID,
                    wiring_generation=WIRING_GENERATION,
                    source_node="node-a",
                    measurements={"node-b": _measurement(status="failed", rtt_ms=None)},
                )
            },
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            now=NOW,
        )
