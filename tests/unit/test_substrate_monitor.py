# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for Node Agent substrate peer reference tracking."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from nodalarc.substrate.measurement_contract import (
    RequiredSubstratePair,
    SubstrateMeasurement,
    decode_status_configmap_data,
)
from node_agent import substrate_monitor

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64


def setup_function() -> None:
    substrate_monitor._reset_for_tests()
    substrate_monitor.set_identity(SESSION_ID, WIRING_GENERATION)


def _ref(vni: int, local_ifname: str) -> substrate_monitor.PeerRef:
    return substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        remote_ip="10.0.0.2",
        vni=vni,
        local_ifname=local_ifname,
    )


def test_exact_peer_refs_keep_peer_active_until_last_ref_removed() -> None:
    ref_a = _ref(1001, "isl0")
    ref_b = _ref(1002, "isl1")

    substrate_monitor.add_peer_ref(ref_a)
    substrate_monitor.add_peer_ref(ref_b)

    assert substrate_monitor.get_active_peers() == ["10.0.0.2"]
    assert substrate_monitor.get_active_refs() == [ref_a, ref_b]

    assert substrate_monitor.remove_peer_ref(ref_a) is True
    assert substrate_monitor.get_active_peers() == ["10.0.0.2"]
    assert substrate_monitor.get_active_refs() == [ref_b]

    assert substrate_monitor.remove_peer_ref(ref_b) is True
    assert substrate_monitor.get_active_peers() == []
    assert substrate_monitor.get_active_refs() == []


def test_peer_ref_rejects_wrong_generation() -> None:
    ref = substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation="sha256:" + "b" * 64,
        remote_ip="10.0.0.2",
        vni=1001,
        local_ifname="isl0",
    )

    with pytest.raises(ValueError, match="identity does not match"):
        substrate_monitor.add_peer_ref(ref)


def test_peer_ref_requires_exact_identity_fields() -> None:
    ref = substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        remote_ip="",
        vni=1001,
        local_ifname="isl0",
    )

    with pytest.raises(ValueError, match="remote_ip"):
        substrate_monitor.add_peer_ref(ref)


def _manifest(
    *,
    session_id: str = SESSION_ID,
    wiring_generation: str = WIRING_GENERATION,
    source_node: str = "node-a",
    source_ip: str = "10.0.0.1",
    target_node: str = "node-b",
    target_ip: str = "10.0.0.2",
) -> WiringManifest:
    pair = RequiredSubstratePair.build(
        source_node=source_node,
        source_ip=source_ip,
        target_node=target_node,
        target_ip=target_ip,
        reasons=["isl"],
    )
    return WiringManifest.model_validate(
        {
            "session_id": session_id,
            "wiring_generation": wiring_generation,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                "sat-a": {
                    "node_type": "satellite",
                    "plane": 0,
                    "slot": 0,
                    "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                    "isl_interfaces": [],
                    "gnd_interfaces": [],
                    "mpls_enable": True,
                    "segment_routing": False,
                    "mtu": 9000,
                    "remove_default_route": True,
                }
            },
            "ground_bridges": {},
            "required_substrate_pairs": [pair.model_dump(mode="json")],
            "isl_link_count": 0,
        }
    )


def _measurement(
    pair: RequiredSubstratePair,
    *,
    session_id: str = SESSION_ID,
    status: str = "ok",
    wiring_generation: str = WIRING_GENERATION,
    stale: bool = False,
) -> SubstrateMeasurement:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    measured_at = now - timedelta(seconds=120) if stale else now
    stale_after = now - timedelta(seconds=1) if stale else now + timedelta(seconds=120)
    return SubstrateMeasurement(
        session_id=session_id,
        wiring_generation=wiring_generation,
        source_node=pair.source_node,
        source_ip=pair.source_ip,
        target_node=pair.target_node,
        target_ip=pair.target_ip,
        measured_at=measured_at,
        stale_after=stale_after,
        status=status,
        sample_count=10,
        success_count=10 if status == "ok" else 0,
        median_rtt_ms=1.25 if status == "ok" else None,
        min_rtt_ms=1.0 if status == "ok" else None,
        max_rtt_ms=1.5 if status == "ok" else None,
        error_message="" if status == "ok" else "ping failed",
    )


def test_manifest_required_measurements_write_status_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()

    document = substrate_monitor.configure_required_measurements(
        v1=v1,
        namespace="nodalarc",
        hostname="node-a",
        manifest=_manifest(),
        measure_fn=lambda pair: _measurement(pair),
    )

    assert document is not None
    assert document.measurements["node-b"].median_rtt_ms == 1.25
    body = v1.create_namespaced_config_map.call_args.args[1]
    decoded = decode_status_configmap_data(body.data)
    assert decoded == document


def test_failed_required_measurement_is_written_then_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()

    with pytest.raises(RuntimeError, match="required substrate measurements failed"):
        substrate_monitor.configure_required_measurements(
            v1=v1,
            namespace="nodalarc",
            hostname="node-a",
            manifest=_manifest(),
            measure_fn=lambda pair: _measurement(pair, status="failed"),
        )

    body = v1.create_namespaced_config_map.call_args.args[1]
    decoded = decode_status_configmap_data(body.data)
    assert decoded.measurements["node-b"].status == "failed"


def test_required_measurement_rejects_host_ip_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.99")
    v1 = MagicMock()

    with pytest.raises(RuntimeError, match="HOST_IP mismatch"):
        substrate_monitor.configure_required_measurements(
            v1=v1,
            namespace="nodalarc",
            hostname="node-a",
            manifest=_manifest(),
            measure_fn=lambda pair: _measurement(pair),
        )

    assert not v1.create_namespaced_config_map.called


def test_cross_node_mutation_defense_requires_fresh_local_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    document = substrate_monitor.configure_required_measurements(
        v1=v1,
        namespace="nodalarc",
        hostname="node-a",
        manifest=_manifest(),
        measure_fn=lambda pair: _measurement(pair),
    )

    substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")

    assert document is not None
    assert document.measurements["node-b"].status == "ok"
    assert v1.create_namespaced_config_map.call_count == 1
    with pytest.raises(RuntimeError, match="no local substrate measurement"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.3")


def test_cross_node_mutation_defense_rejects_missing_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")

    with pytest.raises(RuntimeError, match="has not been measured"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")


def test_cross_node_mutation_defense_rejects_failed_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    with pytest.raises(RuntimeError, match="failed"):
        substrate_monitor.configure_required_measurements(
            v1=v1,
            namespace="nodalarc",
            hostname="node-a",
            manifest=_manifest(),
            measure_fn=lambda pair: _measurement(pair, status="failed"),
        )

    with pytest.raises(ValueError, match="failed"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")


def test_cross_node_mutation_defense_rejects_stale_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    with pytest.raises(RuntimeError, match="stale"):
        substrate_monitor.configure_required_measurements(
            v1=v1,
            namespace="nodalarc",
            hostname="node-a",
            manifest=_manifest(),
            measure_fn=lambda pair: _measurement(pair, stale=True),
        )

    with pytest.raises(ValueError, match="stale"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")


def test_cross_node_mutation_defense_rejects_wrong_generation_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    substrate_monitor.configure_required_measurements(
        v1=v1,
        namespace="nodalarc",
        hostname="node-a",
        manifest=_manifest(),
        measure_fn=lambda pair: _measurement(pair),
    )
    substrate_monitor.set_identity(SESSION_ID, "sha256:" + "b" * 64)

    with pytest.raises(RuntimeError, match="has not been measured"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")


def test_identity_change_clears_epoch_scoped_status_and_peer_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    substrate_monitor.configure_required_measurements(
        v1=v1,
        namespace="nodalarc",
        hostname="node-a",
        manifest=_manifest(),
        measure_fn=lambda pair: _measurement(pair),
    )
    substrate_monitor.add_peer_ref(_ref(1001, "isl0"))

    substrate_monitor.set_identity(SESSION_ID, "sha256:" + "c" * 64)

    assert substrate_monitor.latest_status_document() is None
    assert substrate_monitor.get_active_refs() == []
    with pytest.raises(RuntimeError, match="has not been measured"):
        substrate_monitor.require_fresh_measurement_for_remote_ip("10.0.0.2")


def test_superseded_measurement_is_discarded_without_writing_stale_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_IP", "10.0.0.1")
    v1 = MagicMock()
    next_generation = "sha256:" + "d" * 64

    def superseding_measure(pair: RequiredSubstratePair) -> SubstrateMeasurement:
        substrate_monitor.set_identity(SESSION_ID, next_generation)
        return _measurement(pair)

    document = substrate_monitor.configure_required_measurements(
        v1=v1,
        namespace="nodalarc",
        hostname="node-a",
        manifest=_manifest(),
        measure_fn=superseding_measure,
    )

    assert document is None
    assert substrate_monitor.latest_status_document() is None
    assert not v1.create_namespaced_config_map.called
