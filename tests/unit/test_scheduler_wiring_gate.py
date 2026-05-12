# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Scheduler startup wiring gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from node_agent.manifest_contract import REQUIRED_WIRING_PHASES
from node_agent.wiring_status import NodeWiringStatus, WiringPhaseResult
from scheduler.__main__ import wait_for_wiring_gate

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64


def _ready_node(node_id: str) -> str:
    return NodeWiringStatus(
        node_id=node_id,
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        status="ready",
        phases=[WiringPhaseResult(phase=phase, status="ready") for phase in REQUIRED_WIRING_PHASES],
        dirty_kernel=False,
    ).model_dump_json()


class _K8s:
    def __init__(self, wired: set[str]) -> None:
        self.data = {
            "_session_id": SESSION_ID,
            "_wiring_generation": WIRING_GENERATION,
        }
        self.data.update({node: _ready_node(node) for node in wired})

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
