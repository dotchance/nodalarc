"""Integration test: Node Agent netem state is verifiable in the kernel.

This closes the first leg of the substrate proof harness: the same
namespace_ops functions used by BatchLinkUp/SetLatency must leave an
auditable tc/netem qdisc in the target namespace. The test is marked
requires_root because Linux network namespaces and qdisc mutation require
CAP_NET_ADMIN.

TODO(trust-gap-closure#8): E2E substrate proof (Gate C). This test verifies
tc/netem application in an isolated namespace. The full authority chain —
OME computes orbital_one_way_ms, Scheduler derives netem_one_way_ms via
half-RTT substrate compensation, Node Agent applies tc — is not yet tested
end-to-end in a single test. Add an integration test that runs the full
chain from OME compute_step through latency compensator to namespace_ops
to tc qdisc verification to ping RTT measurement. Assert tc qdisc state
deterministically; treat ping RTT as informational with documented jitter.

TODO(trust-gap-closure#9): Scale characterization (Gate D). Add benchmark
fixtures (separate from unit tests) for 176-sat and 1k-sat OME ticks
measuring P50/P95/P99 compute_step time. Add Scheduler reconcile benchmark
with mock agent pool. Tie budgets to a named reference machine or express
as ratio of sim step interval.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

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


def _qdisc_text(namespace: str, ifname: str) -> str:
    return _run("ip", "netns", "exec", namespace, "tc", "qdisc", "show", "dev", ifname).stdout


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
        qdisc = _qdisc_text(namespace, "isl0")
        assert "tbf" in qdisc
        assert "netem" in qdisc
        assert "delay 12ms" in qdisc or "delay 12.0ms" in qdisc

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


def test_handle_set_latency_proves_kernel_qdisc_state():
    _require_netns_tools()

    from nodalarc.proto import node_agent_pb2
    from node_agent import namespace_ops
    from node_agent.command_contract import RuntimeFence
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
        generation = "sha256:" + "a" * 64
        request = node_agent_pb2.SetLatencyRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id="root-set-latency",
                session_id="root-test",
                wiring_generation=generation,
                operation_kind="SetLatency",
            ),
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
            fence=RuntimeFence(session_id="root-test", wiring_generation=generation),
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
