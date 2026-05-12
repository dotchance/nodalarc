# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Scheduler startup wiring gate."""

from __future__ import annotations

import base64
import gzip
import json
from types import SimpleNamespace

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES
from nodalarc.substrate.wiring_status import NodeWiringStatus, WiringPhaseResult
from scheduler.__main__ import wait_for_wiring_gate, wait_for_wiring_manifest_identity

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

    def read_namespaced_config_map(self, _name: str, _namespace: str):
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
    wait_for_wiring_gate(
        k8s_v1=_K8s({"sat-a", "sat-b"}),
        namespace="nodalarc",
        expected_nodes={"sat-a", "sat-b"},
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        timeout_s=2.0,
        poll_s=0.0,
        sleep=lambda _seconds: None,
    )


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
    _assert_gate_timeout(_node_status("sat-a", dirty_kernel=True))


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
