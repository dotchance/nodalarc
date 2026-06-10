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
    target_node: str,
    target_ip: str,
    reason: str,
) -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
    from nodalarc.substrate.measurement_contract import (
        RequiredSubstratePair,
        SubstrateMeasurement,
    )
    from node_agent import substrate_monitor

    pair = RequiredSubstratePair.build(
        source_node=source_node,
        source_ip=source_ip,
        target_node=target_node,
        target_ip=target_ip,
        reasons=[reason],
    )
    manifest = WiringManifest.model_validate(
        {
            "session_id": "root-test",
            "wiring_generation": generation,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                source_node: {
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
            "site_lans": {},
            "required_substrate_pairs": [pair.model_dump(mode="json")],
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
                pid_map={node_a: proc_a.pid, node_b: proc_b.pid},
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
                pid_map={node_a: proc_a.pid, node_b: proc_b.pid},
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
                pid_map={gs_id: gs_proc.pid, sat_id: sat_proc.pid},
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
                pid_map={gs_id: gs_proc.pid, sat_id: sat_proc.pid},
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
    from node_agent import handlers, kernel_verifier, substrate_monitor, vxlan
    from node_agent.handlers import handle_batch_link_down, handle_batch_link_up

    suffix = uuid.uuid4().hex[:6]
    node_id = f"sat-x{suffix}"
    generation = _generation()
    subnet_octet = int(suffix[:2], 16)
    local_ip = f"198.18.{subnet_octet}.1"
    remote_ip = f"198.18.{subnet_octet}.2"
    dummy = f"na-d{suffix}"[:15]
    vni = 10000 + int(suffix[:4], 16)
    vxlan_if, veth_host, _ = vxlan._host_ifnames(vni)
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        target_node="root-remote",
        target_ip=remote_ip,
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
                pid_map={node_id: proc.pid},
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
                pid_map={node_id: proc.pid},
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
    from node_agent import ground_bridge, handlers, kernel_verifier, substrate_monitor, vxlan
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
    vxlan_if, _, _ = vxlan._host_ifnames(vni)
    monkeypatch.setenv("HOST_IP", local_ip)
    monkeypatch.setattr(handlers, "_local_ip", None)
    _bootstrap_substrate_identity(generation)
    _seed_substrate_measurement(
        generation,
        source_node="root-local",
        source_ip=local_ip,
        target_node="root-remote",
        target_ip=remote_ip,
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
                pid_map={sat_id: proc.pid},
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
                pid_map={sat_id: proc.pid},
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
            pid_map={"sat-a": proc.pid},
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
