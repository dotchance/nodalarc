"""Integration test: Node Agent netem state is verifiable in the kernel.

This is the commercial MVP substrate proof harness. The tests run through the
same Node Agent handlers used by production NATS commands and assert the
resulting kernel state for local ISL, local ground, cross-node ISL,
cross-node ground, qdisc, VXLAN, mirred, cleanup, and SetLatency behavior.

The later hardening lane still needs two-node e2e and scale characterization;
those are not substitutes for these local root proofs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from nodalarc.runtime_naming import vxlan_host_ifnames

pytestmark = [pytest.mark.integration, pytest.mark.requires_root]


def _require_netns_tools() -> None:
    if os.geteuid() != 0:
        pytest.skip("requires root/CAP_NET_ADMIN")
    missing = [tool for tool in ("ip", "tc") if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"missing required network tool(s): {', '.join(missing)}")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
    )


def _run_optional(*args: str) -> None:
    subprocess.run(list(args), capture_output=True, text=True, check=False)


def _qdisc_text(namespace: str, ifname: str) -> str:
    return _run("ip", "netns", "exec", namespace, "tc", "qdisc", "show", "dev", ifname).stdout


def _assert_qdisc(namespace: str, ifname: str, delay_ms: float) -> None:
    qdisc = _qdisc_text(namespace, ifname)
    delay_int = int(delay_ms)
    assert "tbf" in qdisc
    assert "netem" in qdisc
    assert f"delay {delay_int}ms" in qdisc or f"delay {float(delay_ms)}ms" in qdisc


def _handles(pids: dict[str, int]) -> dict:
    """Wrap live unshared-process PIDs in validated namespace handles."""
    from node_agent.pid_discovery import NamespaceHandle, netns_identity

    wrapped = {}
    for node_id, pid in pids.items():
        netns = netns_identity(pid)
        assert netns is not None, f"unshared process {pid} for {node_id} has no netns"
        wrapped[node_id] = NamespaceHandle(
            node_id=node_id,
            pod_uid=f"pod-{node_id}",
            sandbox_id=f"sb-{node_id}",
            sandbox_attempt=0,
            pid=pid,
            netns_id=netns,
        )
    return wrapped


@contextmanager
def _netns(prefix: str) -> Iterator[tuple[str, subprocess.Popen[str]]]:
    suffix = uuid.uuid4().hex[:8]
    namespace = f"na-{prefix}-{suffix}"
    proc: subprocess.Popen[str] | None = None
    try:
        _run("ip", "netns", "add", namespace)
        proc = subprocess.Popen(["ip", "netns", "exec", namespace, "sleep", "120"], text=True)
        time.sleep(0.1)
        if proc.poll() is not None:
            raise RuntimeError(f"namespace keeper process exited for {namespace}")
        yield namespace, proc
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        _run_optional("ip", "netns", "del", namespace)


def _env(kind: str, op_id: str, generation: str):
    from nodalarc.proto import node_agent_pb2

    return node_agent_pb2.CommandEnvelope(
        operation_id=op_id,
        session_id="root-test",
        wiring_generation=generation,
        operation_kind=kind,
    )


def _fence(generation: str):
    from node_agent.command_contract import RuntimeFence

    return RuntimeFence(session_id="root-test", wiring_generation=generation)


def _generation() -> str:
    return "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex


def _bootstrap_substrate_identity(generation: str) -> None:
    from node_agent import substrate_monitor

    substrate_monitor._reset_for_tests()
    substrate_monitor.set_identity("root-test", generation)


def _seed_substrate_measurement(
    generation: str,
    *,
    source_node: str,
    source_ip: str,
    targets: dict[str, str],
    reason: str,
) -> None:
    """Install one substrate snapshot holding a measurement per target node -> IP."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
    from nodalarc.substrate.measurement_contract import (
        RequiredSubstratePair,
        SubstrateMeasurement,
    )
    from node_agent import substrate_monitor

    pairs = [
        RequiredSubstratePair.build(
            source_node=source_node,
            source_ip=source_ip,
            target_node=target_node,
            target_ip=target_ip,
            reasons=[reason],
        )
        for target_node, target_ip in targets.items()
    ]
    manifest = WiringManifest.model_validate(
        {
            "session_id": "root-test",
            "session_run_id": "run-root-0001",
            "owner_uid": "owner-uid-1",
            "wiring_generation": generation,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                source_node: {
                    "node_type": "satellite",
                    "host": "node02",
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
            "site_lans": {},
            "required_substrate_pairs": [pair.model_dump(mode="json") for pair in pairs],
            "isl_link_count": 0,
        }
    )

    def _measurement(required: RequiredSubstratePair) -> SubstrateMeasurement:
        measured_at = datetime.now(UTC)
        return SubstrateMeasurement(
            session_id="root-test",
            wiring_generation=generation,
            source_node=required.source_node,
            source_ip=required.source_ip,
            target_node=required.target_node,
            target_ip=required.target_ip,
            measured_at=measured_at,
            stale_after=measured_at + timedelta(seconds=120),
            status="ok",
            sample_count=10,
            success_count=10,
            median_rtt_ms=1.25,
            min_rtt_ms=1.0,
            max_rtt_ms=1.5,
        )

    substrate_monitor.configure_required_measurements(
        v1=MagicMock(),
        namespace="nodalarc",
        hostname=source_node,
        manifest=manifest,
        measure_fn=_measurement,
    )


def _create_host_dummy(ifname: str, cidr: str) -> None:
    _run("ip", "link", "add", ifname, "type", "dummy")
    _run("ip", "addr", "add", cidr, "dev", ifname)
    _run("ip", "link", "set", ifname, "up")


def test_namespace_ops_apply_and_update_netem_kernel_state():
    _require_netns_tools()

    from node_agent import namespace_ops

    suffix = uuid.uuid4().hex[:8]
    namespace = f"na-netem-{suffix}"
    host_if = f"na-h-{suffix[:6]}"
    peer_if = f"na-p-{suffix[:6]}"
    proc: subprocess.Popen[str] | None = None

    try:
        _run("ip", "netns", "add", namespace)
        _run("ip", "link", "add", host_if, "type", "veth", "peer", "name", peer_if)
        _run("ip", "link", "set", peer_if, "netns", namespace)
        _run("ip", "netns", "exec", namespace, "ip", "link", "set", peer_if, "name", "isl0")
        _run("ip", "netns", "exec", namespace, "ip", "link", "set", "isl0", "up")

        proc = subprocess.Popen(
            ["ip", "netns", "exec", namespace, "sleep", "60"],
            text=True,
        )
        time.sleep(0.1)
        if proc.poll() is not None:
            raise RuntimeError("namespace keeper process exited before shaping test")

        namespace_ops.apply_link_shaping(proc.pid, "isl0", delay_ms=12.0, rate_mbps=1000.0)
        _assert_qdisc(namespace, "isl0", 12.0)

        namespace_ops.update_delay(proc.pid, "isl0", delay_ms=7.0)
        qdisc = _qdisc_text(namespace, "isl0")
        assert "netem" in qdisc
        assert "delay 7ms" in qdisc or "delay 7.0ms" in qdisc

    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        subprocess.run(["ip", "link", "del", host_if], capture_output=True, check=False)
        subprocess.run(["ip", "netns", "del", namespace], capture_output=True, check=False)


def test_handle_batch_link_up_down_proves_local_isl_kernel_state():
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import ground_bridge, kernel_verifier
    from node_agent.handlers import handle_batch_link_down, handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_a = f"sat-r{suffix}a"
    node_b = f"sat-r{suffix}b"
    generation = _generation()
    host_a = ground_bridge._isl_host_name(node_a, 0)
    host_b = ground_bridge._isl_host_name(node_b, 1)

    try:
        with _netns("isl-a") as (ns_a, proc_a), _netns("isl-b") as (ns_b, proc_b):
            ground_bridge.create_mediated_isl(
                proc_a.pid,
                proc_b.pid,
                "isl0",
                "isl1",
                node_a,
                node_b,
            )
            up = node_agent_pb2.BatchLinkUpRequest(
                envelope=_env("BatchLinkUp", "root-local-isl-up", generation),
                interfaces=[
                    node_agent_pb2.InterfaceUp(
                        node_id=node_a,
                        interface_name="isl0",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        latency_ms=6.0,
                        bandwidth_mbps=1000.0,
                        peer_node_id=node_b,
                        peer_interface_name="isl1",
                    ),
                    node_agent_pb2.InterfaceUp(
                        node_id=node_b,
                        interface_name="isl1",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        latency_ms=6.0,
                        bandwidth_mbps=1000.0,
                        peer_node_id=node_a,
                        peer_interface_name="isl0",
                    ),
                ],
            )

            response = handle_batch_link_up(
                up,
                handles=_handles({node_a: proc_a.pid, node_b: proc_b.pid}),
                fence=_fence(generation),
            )

            assert response.success is True
            assert all(result.verified for result in response.interface_results)
            _assert_qdisc(ns_a, "isl0", 6.0)
            _assert_qdisc(ns_b, "isl1", 6.0)
            assert kernel_verifier.verify_host_interface_state(host_a, admin_up=True).verified
            assert kernel_verifier.verify_host_interface_state(host_b, admin_up=True).verified

            down = node_agent_pb2.BatchLinkDownRequest(
                envelope=_env("BatchLinkDown", "root-local-isl-down", generation),
                interfaces=[
                    node_agent_pb2.InterfaceDown(
                        node_id=node_a,
                        interface_name="isl0",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        peer_node_id=node_b,
                        peer_interface_name="isl1",
                    ),
                    node_agent_pb2.InterfaceDown(
                        node_id=node_b,
                        interface_name="isl1",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        peer_node_id=node_a,
                        peer_interface_name="isl0",
                    ),
                ],
            )
            down_response = handle_batch_link_down(
                down,
                handles=_handles({node_a: proc_a.pid, node_b: proc_b.pid}),
                fence=_fence(generation),
            )

            assert down_response.success is True
            assert kernel_verifier.verify_host_interface_state(host_a, admin_up=False).verified
            assert kernel_verifier.verify_host_interface_state(host_b, admin_up=False).verified
    finally:
        _run_optional("ip", "link", "del", host_a)
        _run_optional("ip", "link", "del", host_b)


def test_handle_batch_link_up_down_proves_local_ground_mirred_and_qdisc():
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import ground_bridge, kernel_verifier
    from node_agent.handlers import handle_batch_link_down, handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    gs_id = f"gs-r{suffix}"
    sat_id = f"sat-r{suffix}"
    generation = _generation()
    gs_port = ground_bridge._gs_host_veth(gs_id, "term0")
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")

    try:
        with _netns("gs") as (gs_ns, gs_proc), _netns("sat-gnd") as (sat_ns, sat_proc):
            ground_bridge.create_ground_bridge(gs_id, gs_proc.pid, "term0")
            ground_bridge.create_satellite_ground_veth(sat_id, sat_proc.pid, "gnd0")

            up = node_agent_pb2.BatchLinkUpRequest(
                envelope=_env("BatchLinkUp", "root-local-ground-up", generation),
                interfaces=[
                    node_agent_pb2.InterfaceUp(
                        node_id=gs_id,
                        interface_name="term0",
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        latency_ms=8.0,
                        bandwidth_mbps=100.0,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        peer_node_id=sat_id,
                        peer_interface_name="gnd0",
                    )
                ],
            )
            response = handle_batch_link_up(
                up,
                handles=_handles({gs_id: gs_proc.pid, sat_id: sat_proc.pid}),
                fence=_fence(generation),
            )

            assert response.success is True
            assert response.interface_results[0].verified is True
            assert kernel_verifier.verify_mirred(gs_port, sat_host).verified
            assert kernel_verifier.verify_mirred(sat_host, gs_port).verified
            _assert_qdisc(gs_ns, "term0", 8.0)
            _assert_qdisc(sat_ns, "gnd0", 8.0)

            down = node_agent_pb2.BatchLinkDownRequest(
                envelope=_env("BatchLinkDown", "root-local-ground-down", generation),
                interfaces=[
                    node_agent_pb2.InterfaceDown(
                        node_id=gs_id,
                        interface_name="term0",
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        locality=node_agent_pb2.LOCALITY_LOCAL,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        peer_node_id=sat_id,
                        peer_interface_name="gnd0",
                    )
                ],
            )
            down_response = handle_batch_link_down(
                down,
                handles=_handles({gs_id: gs_proc.pid, sat_id: sat_proc.pid}),
                fence=_fence(generation),
            )

            assert down_response.success is True
            assert kernel_verifier.verify_host_interface_state(gs_port, admin_up=False).verified
            assert kernel_verifier.verify_host_interface_state(sat_host, admin_up=False).verified
    finally:
        _run_optional("ip", "link", "del", gs_port)
        _run_optional("ip", "link", "del", sat_host)


def test_handle_batch_link_up_down_proves_cross_node_isl_vxlan_and_qdisc(monkeypatch):
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import handlers, kernel_verifier, substrate_monitor
    from node_agent.handlers import handle_batch_link_down, handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-x{suffix}"
    generation = _generation()
    subnet_octet = int(suffix[:2], 16)
    local_ip = f"198.18.{subnet_octet}.1"
    remote_ip = f"198.18.{subnet_octet}.2"
    dummy = f"na-d{suffix}"[:15]
    vni = 10000 + int(suffix[:4], 16)
    vxlan_if, veth_host, _ = vxlan_host_ifnames(vni)
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        targets={"root-remote": remote_ip},
        reason="isl",
    )

    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-isl") as (namespace, proc):
            up = node_agent_pb2.BatchLinkUpRequest(
                envelope=_env("BatchLinkUp", "root-cross-isl-up", generation),
                interfaces=[
                    node_agent_pb2.InterfaceUp(
                        node_id=node_id,
                        interface_name="isl0",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                        latency_ms=5.0,
                        bandwidth_mbps=1000.0,
                        peer_node_id="sat-remote",
                        peer_interface_name="isl1",
                        remote_node_ip=remote_ip,
                        vni=vni,
                    )
                ],
            )
            response = handle_batch_link_up(
                up,
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert response.success is True
            assert response.interface_results[0].verified is True
            assert kernel_verifier.verify_vxlan(
                vni, local_ip=local_ip, remote_ip=remote_ip
            ).verified
            _assert_qdisc(namespace, "isl0", 5.0)
            assert [ref.remote_ip for ref in substrate_monitor.get_active_refs()] == [remote_ip]

            down = node_agent_pb2.BatchLinkDownRequest(
                envelope=_env("BatchLinkDown", "root-cross-isl-down", generation),
                interfaces=[
                    node_agent_pb2.InterfaceDown(
                        node_id=node_id,
                        interface_name="isl0",
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                        locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                        peer_node_id="sat-remote",
                        peer_interface_name="isl1",
                        remote_node_ip=remote_ip,
                        vni=vni,
                    )
                ],
            )
            down_response = handle_batch_link_down(
                down,
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert down_response.success is True
            assert substrate_monitor.get_active_refs() == []
            assert kernel_verifier.verify_vxlan_absent(vni).verified
    finally:
        _run_optional("ip", "link", "del", vxlan_if)
        _run_optional("ip", "link", "del", veth_host)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_handle_batch_link_up_down_proves_cross_node_ground_vxlan_mirred_and_qdisc(
    monkeypatch,
):
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import ground_bridge, handlers, kernel_verifier, substrate_monitor
    from node_agent.handlers import handle_batch_link_down, handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    sat_id = f"sat-g{suffix}"
    gs_id = f"gs-g{suffix}"
    generation = _generation()
    subnet_octet = int(suffix[:2], 16)
    local_ip = f"198.19.{subnet_octet}.1"
    remote_ip = f"198.19.{subnet_octet}.2"
    dummy = f"na-d{suffix}"[:15]
    vni = 20000 + int(suffix[:4], 16)
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")
    vxlan_if, _, _ = vxlan_host_ifnames(vni)
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        targets={"root-remote": remote_ip},
        reason="ground",
    )

    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-gnd") as (namespace, proc):
            ground_bridge.create_satellite_ground_veth(sat_id, proc.pid, "gnd0")
            up = node_agent_pb2.BatchLinkUpRequest(
                envelope=_env("BatchLinkUp", "root-cross-ground-up", generation),
                interfaces=[
                    node_agent_pb2.InterfaceUp(
                        node_id=sat_id,
                        interface_name="gnd0",
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                        latency_ms=9.0,
                        bandwidth_mbps=100.0,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        peer_node_id=gs_id,
                        peer_interface_name="term0",
                        remote_node_ip=remote_ip,
                        vni=vni,
                    )
                ],
            )
            response = handle_batch_link_up(
                up,
                handles=_handles({sat_id: proc.pid}),
                fence=_fence(generation),
            )

            assert response.success is True
            assert response.interface_results[0].verified is True
            assert kernel_verifier.verify_vxlan(
                vni, local_ip=local_ip, remote_ip=remote_ip
            ).verified
            assert kernel_verifier.verify_mirred(vxlan_if, sat_host).verified
            assert kernel_verifier.verify_mirred(sat_host, vxlan_if).verified
            _assert_qdisc(namespace, "gnd0", 9.0)
            assert [ref.remote_ip for ref in substrate_monitor.get_active_refs()] == [remote_ip]

            down = node_agent_pb2.BatchLinkDownRequest(
                envelope=_env("BatchLinkDown", "root-cross-ground-down", generation),
                interfaces=[
                    node_agent_pb2.InterfaceDown(
                        node_id=sat_id,
                        interface_name="gnd0",
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        peer_node_id=gs_id,
                        peer_interface_name="term0",
                        remote_node_ip=remote_ip,
                        vni=vni,
                    )
                ],
            )
            down_response = handle_batch_link_down(
                down,
                handles=_handles({sat_id: proc.pid}),
                fence=_fence(generation),
            )

            assert down_response.success is True
            assert substrate_monitor.get_active_refs() == []
            assert kernel_verifier.verify_host_interface_state(sat_host, admin_up=False).verified
            assert kernel_verifier.verify_vxlan_absent(vni).verified
    finally:
        _run_optional("ip", "link", "del", vxlan_if)
        _run_optional("ip", "link", "del", sat_host)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_handle_set_latency_proves_kernel_qdisc_state():
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import namespace_ops
    from node_agent.handlers import handle_set_latency

    suffix = uuid.uuid4().hex[:8]
    namespace = f"na-handler-{suffix}"
    host_if = f"na-h-{suffix[:6]}"
    peer_if = f"na-p-{suffix[:6]}"
    proc: subprocess.Popen[str] | None = None

    try:
        _run("ip", "netns", "add", namespace)
        _run("ip", "link", "add", host_if, "type", "veth", "peer", "name", peer_if)
        _run("ip", "link", "set", peer_if, "netns", namespace)
        _run("ip", "netns", "exec", namespace, "ip", "link", "set", peer_if, "name", "isl0")
        _run("ip", "netns", "exec", namespace, "ip", "link", "set", "isl0", "up")
        proc = subprocess.Popen(["ip", "netns", "exec", namespace, "sleep", "60"], text=True)
        time.sleep(0.1)
        if proc.poll() is not None:
            raise RuntimeError("namespace keeper process exited before handler test")

        namespace_ops.apply_link_shaping(proc.pid, "isl0", delay_ms=12.0, rate_mbps=1000.0)
        generation = _generation()
        request = node_agent_pb2.SetLatencyRequest(
            envelope=_env("SetLatency", "root-set-latency", generation),
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-a",
                    interface_name="isl0",
                    latency_ms=7.0,
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                )
            ],
        )

        response = handle_set_latency(
            request,
            handles=_handles({"sat-a": proc.pid}),
            fence=_fence(generation),
        )

        assert response.success is True
        assert response.entry_results[0].verified is True
        qdisc = _qdisc_text(namespace, "isl0")
        assert "netem" in qdisc
        assert "delay 7ms" in qdisc or "delay 7.0ms" in qdisc

    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        subprocess.run(["ip", "link", "del", host_if], capture_output=True, check=False)
        subprocess.run(["ip", "netns", "del", namespace], capture_output=True, check=False)


def _host_ifindex(ifname: str) -> int | None:
    from pyroute2 import IPRoute

    with IPRoute() as ipr:
        found = ipr.link_lookup(ifname=ifname)
    return found[0] if found else None


def _isl_up(node_id: str, vni: int, remote_ip: str, generation: str, op_id: str):
    from nodalarc.proto import node_agent_pb2

    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_env("BatchLinkUp", op_id, generation),
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id=node_id,
                interface_name="isl0",
                link_type=node_agent_pb2.LINK_TYPE_ISL,
                locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                peer_node_id="sat-remote",
                peer_interface_name="isl1",
                remote_node_ip=remote_ip,
                vni=vni,
            )
        ],
    )


def _cross_isl_setup(monkeypatch, suffix: str, *, octet_base: int, remotes: int = 1):
    """Host identity plus a measured substrate path to ``remotes`` remote hosts."""
    from node_agent import handlers

    generation = _generation()
    subnet_octet = int(suffix[:2], 16)
    local_ip = f"198.{octet_base}.{subnet_octet}.1"
    remote_ips = tuple(f"198.{octet_base}.{subnet_octet}.{2 + n}" for n in range(remotes))
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        targets={f"root-remote-{n}": ip for n, ip in enumerate(remote_ips)},
        reason="isl",
    )
    return generation, local_ip, remote_ips


def test_cross_node_isl_retry_reuses_the_complete_proven_link(monkeypatch):
    """A Scheduler retry after a lost acknowledgement finds the whole link and
    reuses it: same devices, nothing deleted or recreated."""
    _require_netns_tools()
    from node_agent import substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-r{suffix}"
    generation, local_ip, (remote_ip,) = _cross_isl_setup(monkeypatch, suffix, octet_base=20)
    dummy = f"na-d{suffix}"[:15]
    vni = 30000 + int(suffix[:4], 16)
    names = vxlan_host_ifnames(vni)
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-retry") as (_namespace, proc):
            first = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-retry-1"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )
            assert first.success is True
            before = (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth))
            assert None not in before
            # The workload owns the pod interface's admin state after creation:
            # a shutdown there survives the retry and does not refuse it.
            _run("ip", "netns", "exec", _namespace, "ip", "link", "set", "isl0", "down")

            second = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-retry-2"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert second.success is True
            assert second.interface_results[0].verified is True
            assert (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth)) == before
            pod_link = _run("ip", "netns", "exec", _namespace, "ip", "-o", "link", "show", "isl0")
            assert "UP" not in pod_link.stdout.split(">")[0].split("<")[1].split(",")
            host_veth = _run("ip", "-o", "link", "show", names.host_veth).stdout
            assert "UP" in host_veth.split(">")[0].split("<")[1].split(",")
    finally:
        _run_optional("ip", "link", "del", names.host_veth)
        _run_optional("ip", "link", "del", names.tunnel)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_cross_node_isl_refuses_a_complete_link_whose_pod_mtu_differs(monkeypatch):
    """The pod interface's MTU is a fact NodalArc set at creation; a link whose
    pod end carries another MTU is not the requested link and is refused with
    the device and both MTUs named, nothing reconfigured."""
    _require_netns_tools()
    from node_agent import substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-m{suffix}"
    generation, local_ip, (remote_ip,) = _cross_isl_setup(monkeypatch, suffix, octet_base=24)
    dummy = f"na-d{suffix}"[:15]
    vni = 60000 + int(suffix[:4], 16)
    names = vxlan_host_ifnames(vni)
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-mtu") as (_namespace, proc):
            first = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-mtu-1"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )
            assert first.success is True
            before = (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth))
            _run("ip", "netns", "exec", _namespace, "ip", "link", "set", "isl0", "mtu", "1200")

            second = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-mtu-2"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert second.interface_results[0].verified is False
            message = second.interface_results[0].error_message
            assert "not the requested link" in message
            assert "pod isl0 MTU mismatch" in message
            assert (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth)) == before
            pod_link = _run("ip", "netns", "exec", _namespace, "ip", "-o", "link", "show", "isl0")
            assert "mtu 1200" in pod_link.stdout
    finally:
        _run_optional("ip", "link", "del", names.host_veth)
        _run_optional("ip", "link", "del", names.tunnel)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_cross_node_isl_refuses_a_complete_link_to_another_endpoint(monkeypatch):
    """Under the same names, a whole link to a different remote is another
    link: the request is refused and that link is left exactly as it was."""
    _require_netns_tools()
    from node_agent import substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-c{suffix}"
    generation, local_ip, (remote_ip, other_remote) = _cross_isl_setup(
        monkeypatch, suffix, octet_base=21, remotes=2
    )
    dummy = f"na-d{suffix}"[:15]
    vni = 40000 + int(suffix[:4], 16)
    names = vxlan_host_ifnames(vni)
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-conflict") as (_namespace, proc):
            first = handle_batch_link_up(
                _isl_up(node_id, vni, other_remote, generation, "root-conflict-1"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )
            assert first.success is True
            before = (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth))

            second = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-conflict-2"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert second.interface_results[0].verified is False
            assert "not the requested link" in second.interface_results[0].error_message
            assert (_host_ifindex(names.tunnel), _host_ifindex(names.host_veth)) == before
            assert _run("ip", "-d", "link", "show", names.tunnel).stdout.count(other_remote) == 1
    finally:
        _run_optional("ip", "link", "del", names.host_veth)
        _run_optional("ip", "link", "del", names.tunnel)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_cross_node_isl_refuses_a_partial_link_and_leaves_it(monkeypatch):
    """A tunnel with no pair under the link's names is partial state: refused, untouched."""
    _require_netns_tools()
    from node_agent import substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-p{suffix}"
    generation, local_ip, (remote_ip,) = _cross_isl_setup(monkeypatch, suffix, octet_base=22)
    dummy = f"na-d{suffix}"[:15]
    vni = 50000 + int(suffix[:4], 16)
    names = vxlan_host_ifnames(vni)
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        _run(
            "ip",
            "link",
            "add",
            names.tunnel,
            "type",
            "vxlan",
            "id",
            str(vni),
            "local",
            local_ip,
            "remote",
            remote_ip,
            "dstport",
            "4789",
        )
        before = _host_ifindex(names.tunnel)
        with _netns("x-partial") as (_namespace, proc):
            response = handle_batch_link_up(
                _isl_up(node_id, vni, remote_ip, generation, "root-partial"),
                handles=_handles({node_id: proc.pid}),
                fence=_fence(generation),
            )

            assert response.interface_results[0].verified is False
            assert "incomplete link" in response.interface_results[0].error_message
            assert _host_ifindex(names.tunnel) == before
            assert _host_ifindex(names.host_veth) is None
    finally:
        _run_optional("ip", "link", "del", names.host_veth)
        _run_optional("ip", "link", "del", names.tunnel)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def test_vnis_folded_together_by_the_retired_rule_coexist_on_one_host(monkeypatch):
    """VNI 1 and VNI 100000 shared host names under the five-digit rule; both
    links now stand on one host at once."""
    _require_netns_tools()
    from node_agent import kernel_verifier, substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    generation, local_ip, (remote_ip,) = _cross_isl_setup(monkeypatch, suffix, octet_base=23)
    dummy = f"na-d{suffix}"[:15]
    pairs = {1: f"sat-a{suffix}", 100000: f"sat-b{suffix}"}
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        with _netns("x-one") as (_ns_a, proc_a), _netns("x-two") as (_ns_b, proc_b):
            pids = {pairs[1]: proc_a.pid, pairs[100000]: proc_b.pid}
            for vni, node_id in pairs.items():
                response = handle_batch_link_up(
                    _isl_up(node_id, vni, remote_ip, generation, f"root-fold-{vni}"),
                    handles=_handles({node_id: pids[node_id]}),
                    fence=_fence(generation),
                )
                assert response.success is True, vni
            for vni in pairs:
                assert kernel_verifier.verify_vxlan(
                    vni, local_ip=local_ip, remote_ip=remote_ip
                ).verified
            assert len({vxlan_host_ifnames(vni).tunnel for vni in pairs}) == 2
    finally:
        for vni in pairs:
            names = vxlan_host_ifnames(vni)
            _run_optional("ip", "link", "del", names.host_veth)
            _run_optional("ip", "link", "del", names.tunnel)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def _tc(*args: str) -> str:
    return _run("tc", *args).stdout


def _ingress_state(ifname: str) -> str:
    """The interface's ingress side as tc reports it: its ingress-parent qdisc and every filter."""
    qdiscs = [
        line for line in _tc("qdisc", "show", "dev", ifname).splitlines() if "ffff:fff1" in line
    ]
    return "\n".join(qdiscs) + "\n" + _tc("filter", "show", "dev", ifname, "ingress")


_FOREIGN_INGRESS = {
    "redirect elsewhere": lambda dev, other: (
        _tc("qdisc", "add", "dev", dev, "ingress"),
        _tc(
            "filter",
            "add",
            "dev",
            dev,
            "ingress",
            "u32",
            "match",
            "u32",
            "0",
            "0",
            "action",
            "mirred",
            "egress",
            "redirect",
            "dev",
            other,
        ),
    ),
    "direct-action bpf": lambda dev, other: (
        _tc("qdisc", "add", "dev", dev, "ingress"),
        _tc("filter", "add", "dev", dev, "ingress", "bpf", "bytecode", "1,6 0 0 0,", "da"),
    ),
    "u32 police": lambda dev, other: (
        _tc("qdisc", "add", "dev", dev, "ingress"),
        _tc(
            "filter",
            "add",
            "dev",
            dev,
            "ingress",
            "u32",
            "match",
            "u32",
            "0",
            "0",
            "police",
            "rate",
            "1mbit",
            "burst",
            "10k",
            "drop",
        ),
    ),
    "clsact": lambda dev, other: (_tc("qdisc", "add", "dev", dev, "clsact"),),
}


@pytest.mark.parametrize("occupant", sorted(_FOREIGN_INGRESS))
def test_cross_node_ground_refuses_an_occupied_local_interface(monkeypatch, occupant):
    """Whatever occupies the local interface's ingress side, the attach is
    refused before anything is created and the occupant is left as it was."""
    _require_netns_tools()
    from nodalarc.proto import node_agent_pb2
    from node_agent import ground_bridge, handlers, kernel_verifier, substrate_monitor
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    sat_id = f"sat-i{suffix}"
    gs_id = f"gs-i{suffix}"
    generation = _generation()
    subnet_octet = int(suffix[:2], 16)
    local_ip = f"198.24.{subnet_octet}.1"
    remote_ip = f"198.24.{subnet_octet}.2"
    dummy = f"na-d{suffix}"[:15]
    foreign = f"na-f{suffix}"[:15]
    vni = 60000 + int(suffix[:4], 16)
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        targets={"root-remote": remote_ip},
        reason="ground",
    )
    try:
        _create_host_dummy(dummy, f"{local_ip}/24")
        _run("ip", "link", "add", foreign, "type", "dummy")
        with _netns("x-ingress") as (_namespace, proc):
            ground_bridge.create_satellite_ground_veth(sat_id, proc.pid, "gnd0")
            _run("ip", "link", "set", sat_host, "up")
            _FOREIGN_INGRESS[occupant](sat_host, foreign)
            before = _ingress_state(sat_host)
            assert before.strip()

            response = handle_batch_link_up(
                node_agent_pb2.BatchLinkUpRequest(
                    envelope=_env("BatchLinkUp", "root-ingress-occupied", generation),
                    interfaces=[
                        node_agent_pb2.InterfaceUp(
                            node_id=sat_id,
                            interface_name="gnd0",
                            link_type=node_agent_pb2.LINK_TYPE_GROUND,
                            locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                            latency_ms=9.0,
                            bandwidth_mbps=100.0,
                            gs_id=gs_id,
                            sat_id=sat_id,
                            peer_node_id=gs_id,
                            peer_interface_name="term0",
                            remote_node_ip=remote_ip,
                            vni=vni,
                        )
                    ],
                ),
                handles=_handles({sat_id: proc.pid}),
                fence=_fence(generation),
            )

            assert response.interface_results[0].verified is False
            assert "not the requested link" in response.interface_results[0].error_message
            assert "incomplete link" in response.interface_results[0].error_message
            assert kernel_verifier.verify_vxlan_absent(vni).verified
            assert _ingress_state(sat_host) == before
    finally:
        _run_optional("ip", "link", "del", vxlan_host_ifnames(vni).tunnel)
        _run_optional("ip", "link", "del", sat_host)
        _run_optional("ip", "link", "del", foreign)
        _run_optional("ip", "link", "del", dummy)
        substrate_monitor._reset_for_tests()


def _local_ground_up(gs_id: str, sat_id: str, generation: str, op_id: str):
    from nodalarc.proto import node_agent_pb2

    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_env("BatchLinkUp", op_id, generation),
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id=gs_id,
                interface_name="term0",
                link_type=node_agent_pb2.LINK_TYPE_GROUND,
                locality=node_agent_pb2.LOCALITY_LOCAL,
                latency_ms=8.0,
                bandwidth_mbps=100.0,
                gs_id=gs_id,
                sat_id=sat_id,
                peer_node_id=sat_id,
                peer_interface_name="gnd0",
            )
        ],
    )


def test_local_ground_retry_reuses_the_proven_redirect_pair():
    """A retried local ground LinkUp finds its exact redirects and reuses them."""
    _require_netns_tools()
    from node_agent import ground_bridge
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    gs_id = f"gs-t{suffix}"
    sat_id = f"sat-t{suffix}"
    generation = _generation()
    gs_port = ground_bridge._gs_host_veth(gs_id, "term0")
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")
    try:
        with _netns("gs-t") as (_gs_ns, gs_proc), _netns("sat-t") as (_sat_ns, sat_proc):
            ground_bridge.create_ground_bridge(gs_id, gs_proc.pid, "term0")
            ground_bridge.create_satellite_ground_veth(sat_id, sat_proc.pid, "gnd0")
            handles = _handles({gs_id: gs_proc.pid, sat_id: sat_proc.pid})

            first = handle_batch_link_up(
                _local_ground_up(gs_id, sat_id, generation, "root-local-retry-1"),
                handles=handles,
                fence=_fence(generation),
            )
            assert first.success is True
            before = (_ingress_state(gs_port), _ingress_state(sat_host))

            second = handle_batch_link_up(
                _local_ground_up(gs_id, sat_id, generation, "root-local-retry-2"),
                handles=handles,
                fence=_fence(generation),
            )

            assert second.success is True
            assert second.interface_results[0].verified is True
            assert (_ingress_state(gs_port), _ingress_state(sat_host)) == before
    finally:
        _run_optional("ip", "link", "del", gs_port)
        _run_optional("ip", "link", "del", sat_host)


_SELECTED_REDIRECTS = {
    "protocol all, one destination": (
        "protocol",
        "all",
        "u32",
        "match",
        "u32",
        "0x0a000001",
        "0xffffffff",
        "at",
        "16",
    ),
    "protocol ip only": ("protocol", "ip", "u32", "match", "u32", "0", "0"),
}


@pytest.mark.parametrize("restriction", sorted(_SELECTED_REDIRECTS))
def test_local_ground_refuses_a_redirect_for_selected_packets_only(restriction):
    """A redirect toward the right destination that applies to selected
    packets is not the redirect NodalArc installs: refused, left as it was."""
    _require_netns_tools()
    from node_agent import ground_bridge
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    gs_id = f"gs-s{suffix}"
    sat_id = f"sat-s{suffix}"
    generation = _generation()
    gs_port = ground_bridge._gs_host_veth(gs_id, "term0")
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")
    try:
        with _netns("gs-s") as (_gs_ns, gs_proc), _netns("sat-s") as (_sat_ns, sat_proc):
            ground_bridge.create_ground_bridge(gs_id, gs_proc.pid, "term0")
            ground_bridge.create_satellite_ground_veth(sat_id, sat_proc.pid, "gnd0")
            _tc("qdisc", "add", "dev", gs_port, "ingress")
            _tc(
                "filter",
                "add",
                "dev",
                gs_port,
                "ingress",
                *_SELECTED_REDIRECTS[restriction],
                "action",
                "mirred",
                "egress",
                "redirect",
                "dev",
                sat_host,
            )
            ground_bridge._tc_mirred_redirect(sat_host, gs_port)
            before = (_ingress_state(gs_port), _ingress_state(sat_host))

            response = handle_batch_link_up(
                _local_ground_up(gs_id, sat_id, generation, "root-local-selected"),
                handles=_handles({gs_id: gs_proc.pid, sat_id: sat_proc.pid}),
                fence=_fence(generation),
            )

            assert response.interface_results[0].verified is False
            assert "mirred path contested" in response.interface_results[0].error_message
            assert (_ingress_state(gs_port), _ingress_state(sat_host)) == before
    finally:
        _run_optional("ip", "link", "del", gs_port)
        _run_optional("ip", "link", "del", sat_host)


def test_local_ground_refuses_a_redirect_pair_in_another_chain():
    """The exact redirect pair installed in chain 7 carries no ingress traffic:
    refused, left as it was."""
    _require_netns_tools()
    from node_agent import ground_bridge
    from node_agent.handlers import handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    gs_id = f"gs-c{suffix}"
    sat_id = f"sat-c{suffix}"
    generation = _generation()
    gs_port = ground_bridge._gs_host_veth(gs_id, "term0")
    sat_host = ground_bridge._sat_host_veth(sat_id, "gnd0")
    try:
        with _netns("gs-c") as (_gs_ns, gs_proc), _netns("sat-c") as (_sat_ns, sat_proc):
            ground_bridge.create_ground_bridge(gs_id, gs_proc.pid, "term0")
            ground_bridge.create_satellite_ground_veth(sat_id, sat_proc.pid, "gnd0")
            for src, dst in ((gs_port, sat_host), (sat_host, gs_port)):
                _tc("qdisc", "add", "dev", src, "ingress")
                _tc(
                    "filter",
                    "add",
                    "dev",
                    src,
                    "ingress",
                    "chain",
                    "7",
                    "protocol",
                    "all",
                    "u32",
                    "match",
                    "u32",
                    "0",
                    "0",
                    "action",
                    "mirred",
                    "egress",
                    "redirect",
                    "dev",
                    dst,
                )
            before = (_ingress_state(gs_port), _ingress_state(sat_host))
            assert "chain 7" in before[0]

            response = handle_batch_link_up(
                _local_ground_up(gs_id, sat_id, generation, "root-local-chain"),
                handles=_handles({gs_id: gs_proc.pid, sat_id: sat_proc.pid}),
                fence=_fence(generation),
            )

            assert response.interface_results[0].verified is False
            assert "mirred path contested" in response.interface_results[0].error_message
            assert (_ingress_state(gs_port), _ingress_state(sat_host)) == before
    finally:
        _run_optional("ip", "link", "del", gs_port)
        _run_optional("ip", "link", "del", sat_host)
