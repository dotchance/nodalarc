# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Wiring lifecycle seam: one handle set, complete-or-pending, no destruction.

Incomplete discovery must never lead to cleanup of a working data plane, and
wiring consumes exactly the handle set the caller resolved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from nodalarc.substrate.wiring_status import (
    status_configmap_data,
    wiring_row,
)
from node_agent.pid_discovery import NamespaceHandle
from node_agent.reconcile import HostCleanupReport, wiring_status_is_current
from node_agent.wiring import (
    discover_expected_handles,
    execute_wiring,
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


def _clean_report(*, removed: tuple[str, ...] = ()) -> HostCleanupReport:
    return HostCleanupReport(
        host="node02", removed=removed, failed=(), remaining=(), verification_completed=True
    )


def _handle(node_id: str, netns_id: str = "4026532100") -> NamespaceHandle:
    return NamespaceHandle(
        node_id=node_id,
        pod_uid=f"pod-{node_id}",
        sandbox_id=f"sb-{node_id}",
        sandbox_attempt=0,
        pid=4242,
        netns_id=netns_id,
        mpls_enable=False,
    )


def test_incomplete_discovery_returns_none_and_touches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifecycle regression: incomplete discovery yields pending, and the
    seam performs no kernel operation of any kind — cleanup can only be
    reached by a caller that already holds a complete handle set."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    manifest = _manifest({"sat-a": LOCAL_NODE, "sat-b": LOCAL_NODE})
    with (
        patch(
            "node_agent.wiring.discover_local_pod_handles",
            return_value={"sat-a": _handle("sat-a")},
        ),
        patch("node_agent.reconcile.clean_and_verify_host_state") as clean,
        patch("node_agent.namespace_ops._in_namespace") as in_ns,
    ):
        result = discover_expected_handles(manifest, "testns", {"sat-a", "sat-b"})
    assert result is None
    clean.assert_not_called()
    in_ns.assert_not_called()


def test_discovery_exception_is_pending_not_divergence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    manifest = _manifest({"sat-a": LOCAL_NODE})
    with patch(
        "node_agent.wiring.discover_local_pod_handles",
        side_effect=RuntimeError("transient API failure"),
    ):
        result = discover_expected_handles(manifest, "testns", {"sat-a"})
    assert result is None


def test_complete_discovery_returns_exactly_the_expected_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest({"sat-a": LOCAL_NODE})
    with patch(
        "node_agent.wiring.discover_local_pod_handles",
        return_value={"sat-a": _handle("sat-a"), "stray": _handle("stray")},
    ):
        result = discover_expected_handles(manifest, "testns", {"sat-a"})
    assert result is not None
    assert set(result) == {"sat-a"}


def test_discovery_receives_the_manifest_requirement_for_every_expected_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest's MPLS requirement reaches discovery on every call, so a handle
    rebuilt for replacement or Case B carries the same manifest value."""
    manifest = _manifest({"sat-a": LOCAL_NODE, "sat-b": LOCAL_NODE}, mpls=frozenset({"sat-a"}))
    with patch(
        "node_agent.wiring.discover_local_pod_handles",
        return_value={"sat-a": _handle("sat-a"), "sat-b": _handle("sat-b")},
    ) as discover:
        discover_expected_handles(manifest, "testns", {"sat-a", "sat-b"})
        discover_expected_handles(manifest, "testns", {"sat-a", "sat-b"})
    assert [call.kwargs["requirements"] for call in discover.call_args_list] == [
        {"sat-a": True, "sat-b": False},
        {"sat-a": True, "sat-b": False},
    ]


def test_expected_local_nodes_from_manifest_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_NAME", LOCAL_NODE)
    manifest = _manifest({"sat-a": LOCAL_NODE, "sat-c": "node03"})
    assert expected_local_nodes(manifest) == {"sat-a"}
    monkeypatch.delenv("NODE_NAME")
    with pytest.raises(RuntimeError, match="NODE_NAME"):
        expected_local_nodes(manifest)


def test_execute_wiring_never_discovers(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest({"sat-c": "node03"})
    with patch("node_agent.wiring.discover_local_pod_handles") as discover:
        statuses = execute_wiring(manifest, namespace="testns", handles={})
    assert statuses == {}
    discover.assert_not_called()


def _status_cm(manifest: WiringManifest, rows: dict) -> SimpleNamespace:
    return SimpleNamespace(data=status_configmap_data(rows, manifest))


def test_case_b_binds_rows_to_live_incarnations() -> None:
    manifest = _manifest({"sat-a": LOCAL_NODE})
    wired = {"sat-a": _handle("sat-a")}
    rows = {
        node_id: wiring_row(
            node_id,
            manifest,
            pod_uid=handle.pod_uid,
            sandbox_id=handle.sandbox_id,
            netns_id=handle.netns_id,
            state="ready",
        )
        for node_id, handle in wired.items()
    }
    v1 = SimpleNamespace(read_namespaced_config_map=lambda *_a, **_k: _status_cm(manifest, rows))
    assert wiring_status_is_current(v1, "testns", manifest, wired) is True

    replaced_netns = {"sat-a": _handle("sat-a", netns_id="4026539999")}
    assert wiring_status_is_current(v1, "testns", manifest, replaced_netns) is False

    replaced_sandbox = {
        "sat-a": NamespaceHandle(
            node_id="sat-a",
            pod_uid="pod-sat-a",
            sandbox_id="sb-replacement",
            sandbox_attempt=1,
            pid=4242,
            netns_id="4026532100",
            mpls_enable=False,
        )
    }
    assert wiring_status_is_current(v1, "testns", manifest, replaced_sandbox) is False


def test_rewiring_rows_invalidate_readiness() -> None:
    """The pre-destruction rows must fail every readiness consumer."""
    manifest = _manifest({"sat-a": LOCAL_NODE})
    handle = _handle("sat-a")
    row = wiring_row(
        "sat-a",
        manifest,
        pod_uid=handle.pod_uid,
        sandbox_id=handle.sandbox_id,
        netns_id=handle.netns_id,
        state="wiring",
    )
    assert row.status == "wiring"
    assert row.ready_for(manifest) is False
    v1 = SimpleNamespace(
        read_namespaced_config_map=lambda *_a, **_k: _status_cm(manifest, {"sat-a": row})
    )
    assert wiring_status_is_current(v1, "testns", manifest, {"sat-a": handle}) is False


def test_rewire_transition_order_is_drain_invalidate_withdraw_rebuild_install_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rewire transition function, exercised directly: a batch must
    never observe half-torn state. Order: drain dispatch -> publish
    non-ready -> withdraw handles -> cleanup -> wire -> install handles ->
    publish ready -> resume. Ordering above this call (the watcher loop)
    is enforced by its single call site and proven in the live vertical.
    """
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    manifest = _manifest({"sat-a": LOCAL_NODE})
    handles = {"sat-a": _handle("sat-a")}
    shared: dict = {"sat-a": _handle("sat-a")}
    order: list[str] = []

    gate = MagicMock()
    gate.drain.side_effect = lambda *a, **k: order.append("drain") or True
    gate.resume.side_effect = lambda: order.append("resume")

    writes: list[str] = []

    def _fake_write(statuses, _manifest, namespace):
        kinds = {row.status for row in statuses.values()}
        writes.append("non-ready" if kinds == {"wiring"} else "ready")
        order.append(f"write:{writes[-1]}")

    class _Shared(dict):
        def clear(self):
            order.append("withdraw")
            super().clear()

        def update(self, other):
            order.append("install")
            super().update(other)

    shared = _Shared(shared)

    ready_rows = {
        "sat-a": wiring_row(
            "sat-a",
            manifest,
            pod_uid="pod-sat-a",
            sandbox_id="sb-sat-a",
            netns_id="4026532100",
            state="ready",
        )
    }
    monkeypatch.setattr(na_main, "write_wiring_status", _fake_write)
    monkeypatch.setattr(
        na_main, "get_actual_nodalarc_interfaces", lambda: order.append("inspect") or {"isl0"}
    )
    monkeypatch.setattr(
        na_main,
        "clean_and_verify_host_state",
        lambda: order.append("cleanup") or _clean_report(removed=("isl0",)),
    )
    monkeypatch.setattr(
        na_main,
        "execute_wiring",
        lambda *a, **k: order.append("wire") or ready_rows,
    )

    result = na_main.perform_rewire(manifest, "testns", handles, {"sat-a"}, shared, gate)
    assert result == ready_rows
    assert order == [
        "drain",
        "write:non-ready",
        "withdraw",
        "inspect",
        "cleanup",
        "wire",
        "install",
        "write:ready",
        "resume",
    ]
    assert dict(shared) == handles


def test_failed_rewire_keeps_handles_withdrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    manifest = _manifest({"sat-a": LOCAL_NODE})
    handle = _handle("sat-a")
    shared: dict = {"sat-a": handle}
    gate = MagicMock()
    gate.drain.return_value = True

    failed_rows = {
        "sat-a": wiring_row(
            "sat-a",
            manifest,
            pod_uid=handle.pod_uid,
            sandbox_id=handle.sandbox_id,
            netns_id=handle.netns_id,
            state="wiring",
        )
    }
    monkeypatch.setattr(na_main, "write_wiring_status", lambda *a, **k: None)
    monkeypatch.setattr(na_main, "get_actual_nodalarc_interfaces", lambda: set())
    monkeypatch.setattr(na_main, "execute_wiring", lambda *a, **k: failed_rows)

    with pytest.raises(RuntimeError, match="wiring failed"):
        na_main.perform_rewire(manifest, "testns", {"sat-a": handle}, {"sat-a"}, shared, gate)
    assert shared == {}
    # A destructive rewire failure leaves dispatch CLOSED: no runtime
    # mutation may run until a later pass publishes honest ready proof.
    gate.resume.assert_not_called()


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


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        (
            HostCleanupReport(
                host="node02",
                removed=("isl0",),
                failed=(("vx00abcd", "NetlinkError 16: (16, 'Device or resource busy')"),),
                remaining=("vx00abcd",),
                verification_completed=True,
            ),
            "a failed delete",
        ),
        (
            HostCleanupReport(
                host="node02",
                removed=("isl0",),
                failed=(),
                remaining=("vh00abcd",),
                verification_completed=True,
            ),
            "residue after the deletes",
        ),
        (
            HostCleanupReport(
                host="node02",
                removed=("isl0",),
                failed=(),
                remaining=(),
                verification_completed=False,
                verification_error="OSError: [Errno 24] Too many open files",
            ),
            "a verification that could not complete",
        ),
    ],
)
def test_unclean_cleanup_report_refuses_to_wire_and_keeps_handles_withdrawn(
    monkeypatch: pytest.MonkeyPatch, report: HostCleanupReport, reason: str
) -> None:
    """Wiring from scratch over a failed, incomplete or unverified cleanup is
    refused through the rewire failure path: nothing is wired, handles stay
    withdrawn and dispatch stays closed."""
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    manifest = _manifest({"sat-a": LOCAL_NODE})
    handle = _handle("sat-a")
    shared: dict = {"sat-a": handle}
    gate = MagicMock()
    gate.drain.return_value = True
    execute = MagicMock()
    monkeypatch.setattr(na_main, "write_wiring_status", lambda *a, **k: None)
    monkeypatch.setattr(na_main, "get_actual_nodalarc_interfaces", lambda: {"isl0"})
    monkeypatch.setattr(na_main, "clean_and_verify_host_state", lambda: report)
    monkeypatch.setattr(na_main, "execute_wiring", execute)

    with pytest.raises(RuntimeError, match="not wiring over residue") as raised:
        na_main.perform_rewire(manifest, "testns", {"sat-a": handle}, {"sat-a"}, shared, gate)

    assert reason
    assert f"remaining={list(report.remaining)}" in str(raised.value)
    assert f"verification_completed={report.verification_completed}" in str(raised.value)
    execute.assert_not_called()
    assert shared == {}
    gate.resume.assert_not_called()


def test_drain_timeout_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-flight mutation must never overlap a rebuild: on drain timeout
    the transition performs zero mutations and restores dispatch."""
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    manifest = _manifest({"sat-a": LOCAL_NODE})
    handle = _handle("sat-a")
    shared: dict = {"sat-a": handle}
    gate = MagicMock()
    gate.drain.return_value = False

    write = MagicMock()
    clean = MagicMock()
    execute = MagicMock()
    monkeypatch.setattr(na_main, "write_wiring_status", write)
    monkeypatch.setattr(na_main, "clean_and_verify_host_state", clean)
    monkeypatch.setattr(na_main, "get_actual_nodalarc_interfaces", MagicMock())
    monkeypatch.setattr(na_main, "execute_wiring", execute)

    result = na_main.perform_rewire(manifest, "testns", {"sat-a": handle}, {"sat-a"}, shared, gate)
    assert result is None
    write.assert_not_called()
    clean.assert_not_called()
    execute.assert_not_called()
    assert shared == {"sat-a": handle}
    gate.resume.assert_called_once()


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
    from node_agent.command_contract import RuntimeFence
    from node_agent.server import DispatchGate, dispatch

    gate = DispatchGate()
    assert gate.drain(timeout_seconds=0.5) is True
    fence = RuntimeFence(session_id="s", wiring_generation="sha256:" + "a" * 64)

    raw = dispatch(msg_type + b"\x00", {}, fence, gate)
    response = response_cls()
    response.ParseFromString(raw)
    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_STALE_GENERATION
    assert "rewiring" in response.error_message


@pytest.mark.parametrize("new_handles", [{}, {"sat-a": _handle("sat-a")}])
def test_terminal_handle_swap_defers_on_failed_drain(new_handles) -> None:
    """Both terminal swaps (no-local empty, already-current replacement)
    must leave the map untouched and restore dispatch when drain fails."""
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    existing = _handle("sat-old")
    shared: dict = {"sat-old": existing}
    gate = MagicMock()
    gate.drain.return_value = False

    assert na_main.replace_handles_when_idle(gate, shared, new_handles) is False
    assert shared == {"sat-old": existing}
    gate.resume.assert_called_once()


@pytest.mark.parametrize("new_handles", [{}, {"sat-a": _handle("sat-a")}])
def test_terminal_handle_swap_completes_when_idle(new_handles) -> None:
    from unittest.mock import MagicMock

    from node_agent import __main__ as na_main

    shared: dict = {"sat-old": _handle("sat-old")}
    gate = MagicMock()
    gate.drain.return_value = True

    assert na_main.replace_handles_when_idle(gate, shared, new_handles) is True
    assert shared == new_handles
    gate.resume.assert_called_once()
