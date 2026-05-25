# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for Scheduler startup wiring gate."""

from __future__ import annotations

import base64
import gzip
import json
from types import SimpleNamespace

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES
from nodalarc.substrate.measurement_contract import (
    RequiredSubstratePair,
    SubstrateMeasurement,
    SubstrateStatusDocument,
    status_document_configmap_data,
)
from nodalarc.substrate.wiring_status import NodeWiringStatus, WiringPhaseResult
from scheduler.__main__ import (
    wait_for_substrate_gate,
    wait_for_wiring_gate,
    wait_for_wiring_manifest_identity,
)

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64


def _node_status(
    node_id: str,
    *,
    session_id: str = SESSION_ID,
    wiring_generation: str = WIRING_GENERATION,
    status: str = "ready",
    dirty_kernel: bool = False,
    phase_overrides: dict[str, str] | None = None,
) -> str:
    overrides = phase_overrides or {}
    return NodeWiringStatus(
        node_id=node_id,
        session_id=session_id,
        wiring_generation=wiring_generation,
        status=status,
        phases=[
            WiringPhaseResult(phase=phase, status=overrides.get(phase, "ready"))
            for phase in REQUIRED_WIRING_PHASES
        ],
        dirty_kernel=dirty_kernel,
    ).model_dump_json()


def _ready_node(node_id: str) -> str:
    return _node_status(node_id)


class _K8s:
    def __init__(
        self, wired: set[str] | None = None, statuses: dict[str, str] | None = None
    ) -> None:
        self.data = {
            "_session_id": SESSION_ID,
            "_wiring_generation": WIRING_GENERATION,
        }
        self.data.update({node: _ready_node(node) for node in wired or set()})
        self.data.update(statuses or {})
        self.reads: list[tuple[str, str]] = []

    def read_namespaced_config_map(self, name: str, namespace: str):
        self.reads.append((name, namespace))
        return SimpleNamespace(data=self.data)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now

    def sleep(self, _seconds: float) -> None:
        return None


class _NotFound(Exception):
    status = 404


class _ManifestK8s:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def read_namespaced_config_map(self, name: str, namespace: str):
        assert name == "nodalarc-topology-wiring"
        assert namespace == "nodalarc"
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _manifest_configmap() -> SimpleNamespace:
    manifest = {
        "session_id": SESSION_ID,
        "wiring_generation": WIRING_GENERATION,
        "required_phases": list(REQUIRED_WIRING_PHASES),
        "nodes": {
            "sat-a": {
                "node_type": "satellite",
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [],
                "mpls_enable": True,
                "segment_routing": False,
                "mtu": 1500,
                "remove_default_route": True,
                "plane": 0,
                "slot": 0,
            }
        },
        "ground_bridges": {},
        "required_substrate_pairs": [],
        "isl_link_count": 0,
    }
    encoded = base64.b64encode(gzip.compress(json.dumps(manifest).encode())).decode()
    return SimpleNamespace(data={"manifest.json.gz.b64": encoded})


def test_wiring_manifest_gate_waits_through_creation_race() -> None:
    k8s = _ManifestK8s([_NotFound(), _manifest_configmap()])

    manifest = wait_for_wiring_manifest_identity(
        k8s_v1=k8s,
        namespace="nodalarc",
        timeout_s=5.0,
        poll_s=0.0,
        sleep=lambda _seconds: None,
    )

    assert manifest.session_id == SESSION_ID
    assert manifest.wiring_generation == WIRING_GENERATION
    assert k8s.calls == 2


def test_wiring_manifest_gate_fails_closed_on_missing_manifest_timeout() -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="nodalarc-topology-wiring ConfigMap not found"):
        wait_for_wiring_manifest_identity(
            k8s_v1=_ManifestK8s([_NotFound(), _NotFound(), _NotFound()]),
            namespace="nodalarc",
            timeout_s=2.0,
            poll_s=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )


def test_wiring_manifest_gate_fails_immediately_on_malformed_manifest() -> None:
    with pytest.raises(RuntimeError, match="missing manifest.json.gz.b64"):
        wait_for_wiring_manifest_identity(
            k8s_v1=_ManifestK8s([SimpleNamespace(data={"other": "value"})]),
            namespace="nodalarc",
            timeout_s=5.0,
            poll_s=0.0,
            sleep=lambda _seconds: None,
        )


def test_wiring_gate_passes_when_all_expected_nodes_are_wired() -> None:
    k8s = _K8s({"sat-a", "sat-b"})

    result = wait_for_wiring_gate(
        k8s_v1=k8s,
        namespace="nodalarc",
        expected_nodes={"sat-a", "sat-b"},
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        timeout_s=2.0,
        poll_s=0.0,
        sleep=lambda _seconds: None,
    )

    assert result is None
    assert k8s.reads == [("nodalarc-wiring-status", "nodalarc")]


def test_wiring_gate_fails_closed_on_timeout() -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="Wiring gate timeout"):
        wait_for_wiring_gate(
            k8s_v1=_K8s({"sat-a"}),
            namespace="nodalarc",
            expected_nodes={"sat-a", "sat-b"},
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            timeout_s=2.0,
            poll_s=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )


def _assert_gate_timeout(status_json: str) -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="Wiring gate timeout"):
        wait_for_wiring_gate(
            k8s_v1=_K8s(statuses={"sat-a": status_json}),
            namespace="nodalarc",
            expected_nodes={"sat-a"},
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            timeout_s=2.0,
            poll_s=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )


def test_wiring_gate_rejects_dirty_kernel_status() -> None:
    with pytest.raises(RuntimeError, match="Wiring gate failed"):
        wait_for_wiring_gate(
            k8s_v1=_K8s(statuses={"sat-a": _node_status("sat-a", dirty_kernel=True)}),
            namespace="nodalarc",
            expected_nodes={"sat-a"},
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
            timeout_s=2.0,
            poll_s=0.0,
            sleep=lambda _seconds: None,
        )


def test_wiring_gate_rejects_generation_mismatch() -> None:
    _assert_gate_timeout(_node_status("sat-a", wiring_generation="sha256:" + "b" * 64))


def test_wiring_gate_rejects_session_mismatch() -> None:
    _assert_gate_timeout(_node_status("sat-a", session_id="other-session"))


def test_wiring_gate_rejects_non_ready_status() -> None:
    _assert_gate_timeout(_node_status("sat-a", status="wiring"))


def test_wiring_gate_rejects_incomplete_phase_status() -> None:
    _assert_gate_timeout(
        _node_status(
            "sat-a",
            phase_overrides={"pod_security": "pending_pid"},
        )
    )


def _substrate_pair() -> RequiredSubstratePair:
    return RequiredSubstratePair.build(
        source_node="node-a",
        source_ip="10.0.0.1",
        target_node="node-b",
        target_ip="10.0.0.2",
        reasons=["isl"],
    )


def _substrate_manifest(required_pairs: list[RequiredSubstratePair]):
    from nodalarc.substrate.manifest_contract import WiringManifest

    return WiringManifest.model_validate(
        {
            "session_id": SESSION_ID,
            "wiring_generation": WIRING_GENERATION,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                "sat-a": {
                    "node_type": "satellite",
                    "sysctls": {"net.ipv4.ip_forward": "1"},
                    "isl_interfaces": [],
                    "gnd_interfaces": [],
                    "mpls_enable": True,
                    "segment_routing": False,
                    "mtu": 1500,
                    "remove_default_route": True,
                    "plane": 0,
                    "slot": 0,
                }
            },
            "ground_bridges": {},
            "required_substrate_pairs": [pair.model_dump(mode="json") for pair in required_pairs],
            "isl_link_count": 0,
        }
    )


def _substrate_measurement(status: str = "ok") -> SubstrateMeasurement:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SubstrateMeasurement(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        source_node="node-a",
        source_ip="10.0.0.1",
        target_node="node-b",
        target_ip="10.0.0.2",
        measured_at=now,
        stale_after=now + timedelta(days=365),
        status=status,
        sample_count=10,
        success_count=10 if status == "ok" else 0,
        median_rtt_ms=1.0 if status == "ok" else None,
        min_rtt_ms=0.9 if status == "ok" else None,
        max_rtt_ms=1.1 if status == "ok" else None,
        error_message="" if status == "ok" else "ping failed",
    )


class _SubstrateK8s:
    def __init__(self, documents: list[SubstrateStatusDocument]) -> None:
        self.documents = documents

    def list_namespaced_config_map(self, _namespace: str, label_selector: str):
        assert "substrate-status" in label_selector
        return SimpleNamespace(
            items=[
                SimpleNamespace(data=status_document_configmap_data(document))
                for document in self.documents
            ]
        )


def test_substrate_gate_passes_with_no_required_pairs() -> None:
    assert (
        wait_for_substrate_gate(
            k8s_v1=_SubstrateK8s([]),
            namespace="nodalarc",
            manifest=_substrate_manifest([]),
            timeout_s=1.0,
            poll_s=0.0,
        )
        == {}
    )


def test_substrate_gate_passes_with_complete_measurement() -> None:
    pair = _substrate_pair()
    document = SubstrateStatusDocument(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        source_node="node-a",
        measurements={"node-b": _substrate_measurement()},
    )

    result = wait_for_substrate_gate(
        k8s_v1=_SubstrateK8s([document]),
        namespace="nodalarc",
        manifest=_substrate_manifest([pair]),
        timeout_s=1.0,
        poll_s=0.0,
    )

    assert result["node-a->node-b"].median_rtt_ms == 1.0


def test_substrate_gate_fails_closed_on_missing_measurement() -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="Substrate gate timeout"):
        wait_for_substrate_gate(
            k8s_v1=_SubstrateK8s([]),
            namespace="nodalarc",
            manifest=_substrate_manifest([_substrate_pair()]),
            timeout_s=2.0,
            poll_s=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
