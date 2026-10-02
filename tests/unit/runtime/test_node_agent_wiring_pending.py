# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Wiring lifecycle seam: one handle set, complete-or-pending, no destruction.

Incomplete discovery must never lead to cleanup of a working data plane, and
wiring consumes exactly the handle set the caller resolved.
"""

from __future__ import annotations

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from node_agent.wiring import (
    expected_local_nodes,
)

pytestmark = pytest.mark.usefixtures("_node_agent_ops_spool_path")


LOCAL_NODE = "node02"


def _manifest(hosts: dict[str, str], mpls: frozenset[str] = frozenset()) -> WiringManifest:
    nodes = {}
    for index, (node_id, host) in enumerate(sorted(hosts.items())):
        nodes[node_id] = {
            "node_type": "satellite",
            "host": host,
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "isl_interfaces": [],
            "gnd_interfaces": [],
            "mpls_enable": node_id in mpls,
            "remove_default_route": False,
            "plane": 0,
            "slot": index,
        }
    return WiringManifest.model_validate(
        {
            "session_id": "test-session",
            "session_run_id": "run-test-0001",
            "owner_uid": "owner-uid-1",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": nodes,
            "ground_bridges": {},
            "site_lans": {},
            "required_substrate_pairs": [],
            "isl_link_count": 0,
        }
    )


def test_expected_local_nodes_from_manifest_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_NAME", LOCAL_NODE)
    manifest = _manifest({"sat-a": LOCAL_NODE, "sat-c": "node03"})
    assert expected_local_nodes(manifest) == {"sat-a"}
    monkeypatch.delenv("NODE_NAME")
    with pytest.raises(RuntimeError, match="NODE_NAME"):
        expected_local_nodes(manifest)


def test_dispatch_gate_refuses_while_draining() -> None:
    from node_agent.server import DispatchGate

    gate = DispatchGate()
    assert gate.try_enter() is True
    gate.leave()
    assert gate.drain(timeout_seconds=1.0) is True
    assert gate.try_enter() is False
    gate.resume()
    assert gate.try_enter() is True
    gate.leave()


def test_dispatch_gate_drain_waits_for_inflight() -> None:
    import threading
    import time as time_mod

    from node_agent.server import DispatchGate

    gate = DispatchGate()
    assert gate.try_enter() is True
    result: dict = {}

    def _drainer():
        result["idle"] = gate.drain(timeout_seconds=2.0)

    thread = threading.Thread(target=_drainer)
    thread.start()
    time_mod.sleep(0.1)
    assert gate.try_enter() is False
    gate.leave()
    thread.join(timeout=3.0)
    assert result["idle"] is True


def _refusal_cases():
    from nodalarc.proto import node_agent_pb2

    return [
        (b"BatchLinkDown", node_agent_pb2.BatchLinkDownResponse),
        (b"BatchLinkUp", node_agent_pb2.BatchLinkUpResponse),
        (b"SetLatency", node_agent_pb2.SetLatencyResponse),
        (b"KernelInventory", node_agent_pb2.KernelInventoryResponse),
    ]


@pytest.mark.parametrize(("msg_type", "response_cls"), _refusal_cases())
def test_rewiring_refusal_decodes_as_the_operation_sent(msg_type, response_cls) -> None:
    """Every client decodes its operation-specific response type; the drain
    refusal must round-trip as that exact type with the stale-generation
    code, never as a generic frame that decodes to code 0."""
    from nodalarc.proto import node_agent_pb2
    from node_agent.command_contract import RuntimeFence, WriterEpochFloor
    from node_agent.server import DispatchGate, dispatch

    gate = DispatchGate()
    assert gate.drain(timeout_seconds=0.5) is True
    fence = RuntimeFence(
        session_id="s",
        wiring_generation="sha256:" + "a" * 64,
        writer_floor=WriterEpochFloor(lambda: None),
    )

    raw = dispatch(msg_type + b"\x00", {}, fence, gate)
    response = response_cls()
    response.ParseFromString(raw)
    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_STALE_GENERATION
    assert "rewiring" in response.error_message
