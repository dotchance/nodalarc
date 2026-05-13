# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for durable substrate measurement contract models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.substrate.measurement_contract import (
    RequiredSubstratePair,
    SubstrateMeasurement,
    SubstrateStatusDocument,
    decode_status_configmap_data,
    status_document_configmap_data,
    substrate_directional_key,
    substrate_pair_key,
)
from pydantic import ValidationError

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _measurement(**overrides) -> SubstrateMeasurement:
    values = {
        "session_id": SESSION_ID,
        "wiring_generation": WIRING_GENERATION,
        "source_node": "node-a",
        "source_ip": "10.0.0.1",
        "target_node": "node-b",
        "target_ip": "10.0.0.2",
        "measured_at": NOW,
        "stale_after": NOW + timedelta(seconds=120),
        "status": "ok",
        "sample_count": 10,
        "success_count": 10,
        "median_rtt_ms": 1.25,
        "min_rtt_ms": 1.0,
        "max_rtt_ms": 1.5,
        "error_message": "",
    }
    values.update(overrides)
    return SubstrateMeasurement.model_validate(values)


def test_required_pair_computes_and_validates_keys() -> None:
    pair = RequiredSubstratePair.build(
        source_node="node-b",
        source_ip="10.0.0.2",
        target_node="node-a",
        target_ip="10.0.0.1",
        reasons=["ground", "isl"],
    )

    assert pair.pair_key == "node-a<->node-b"
    assert pair.directional_key == "node-b->node-a"
    assert pair.reasons == ["ground", "isl"]
    assert substrate_pair_key("node-b", "node-a") == "node-a<->node-b"
    assert substrate_directional_key("node-b", "node-a") == "node-b->node-a"


def test_required_pair_rejects_key_mismatch() -> None:
    with pytest.raises(ValidationError, match="pair_key mismatch"):
        RequiredSubstratePair.model_validate(
            {
                "source_node": "node-a",
                "source_ip": "10.0.0.1",
                "target_node": "node-b",
                "target_ip": "10.0.0.2",
                "reasons": ["isl"],
                "pair_key": "wrong",
                "directional_key": "node-a->node-b",
            }
        )


def test_measurement_returns_rtt_only_for_exact_fresh_identity() -> None:
    measurement = _measurement()

    assert (
        measurement.rtt_ms(
            now=NOW + timedelta(seconds=1),
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            source_node="node-a",
            source_ip="10.0.0.1",
            target_node="node-b",
            target_ip="10.0.0.2",
        )
        == 1.25
    )


def test_measurement_rejects_wrong_identity() -> None:
    measurement = _measurement()

    with pytest.raises(ValueError, match="identity mismatch"):
        measurement.rtt_ms(
            now=NOW + timedelta(seconds=1),
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            source_node="node-a",
            source_ip="10.0.0.99",
            target_node="node-b",
            target_ip="10.0.0.2",
        )


def test_measurement_rejects_stale_or_failed() -> None:
    with pytest.raises(ValueError, match="stale"):
        _measurement(stale_after=NOW + timedelta(seconds=1)).rtt_ms(
            now=NOW + timedelta(seconds=2),
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            source_node="node-a",
            source_ip="10.0.0.1",
            target_node="node-b",
            target_ip="10.0.0.2",
        )

    failed = _measurement(status="failed", success_count=0, median_rtt_ms=None, error_message="no")
    with pytest.raises(ValueError, match="failed"):
        failed.rtt_ms(
            now=NOW + timedelta(seconds=1),
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            source_node="node-a",
            source_ip="10.0.0.1",
            target_node="node-b",
            target_ip="10.0.0.2",
        )


def test_status_document_roundtrips_from_configmap_data() -> None:
    document = SubstrateStatusDocument(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        source_node="node-a",
        measurements={"node-b": _measurement()},
    )

    decoded = decode_status_configmap_data(status_document_configmap_data(document))

    assert decoded == document
