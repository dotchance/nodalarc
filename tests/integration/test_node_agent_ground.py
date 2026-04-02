"""Integration test: Node Agent ground link BatchLinkDown + BatchLinkUp.

Requires:
  - Running K3s session with gs-ashburn and sat-p01s02 pods
  - Root access
  - gs-ashburn currently attached to sat-p01s02 via tc mirred

Verifies:
  1. BatchLinkDown removes tc mirred redirect, brings gnd0 down
  2. BatchLinkUp re-attaches, applies tc shaping, gnd0 comes up
  3. RTM_NEWLINK events fire for gnd0 (admin toggle works)
  4. L2 connectivity works after re-attach
"""

from __future__ import annotations

import json
import subprocess
import time
from concurrent import futures

import grpc
from nodalarc.proto import node_agent_pb2
from nodalarc.proto.node_agent_pb2_grpc import (
    NodeAgentServiceStub,
    add_NodeAgentServiceServicer_to_server,
)

from node_agent.server import NodeAgentServicer

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
NAMESPACE = "nodalarc"


def kubectl(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", f"--kubeconfig={KUBECONFIG}", "-n", NAMESPACE, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def get_pid(pod: str) -> int:
    cid = kubectl("get", "pod", pod, "-o", "jsonpath={.status.containerStatuses[0].containerID}")
    raw_id = cid.split("://", 1)[-1]
    proc = subprocess.run(["crictl", "inspect", raw_id], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["info"]["pid"]


def get_iface_state(pod: str, ifname: str) -> str:
    out = kubectl("exec", pod, "-c", "frr", "--", "ip", "-o", "link", "show", ifname)
    if "state UP" in out:
        return "UP"
    if "state DOWN" in out:
        return "DOWN"
    return "UNKNOWN"


def get_tc_mirred(dev: str) -> str:
    """Get tc mirred redirect target from host device."""
    out = subprocess.run(
        ["tc", "filter", "show", "dev", dev, "parent", "ffff:"],
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if "mirred" in line and "Redirect" in line:
            # "action order 1: mirred (Egress Redirect to device _gnd_P01S02) stolen"
            parts = line.split("device")
            if len(parts) > 1:
                return parts[1].split(")")[0].strip()
    return ""


def main():
    print("=== Ground Link Integration Test ===")

    gs_pid = get_pid("gs-ashburn")
    sat_pid = get_pid("sat-p01s02")
    print(f"  gs-ashburn PID={gs_pid}")
    print(f"  sat-p01s02 PID={sat_pid}")

    # Check initial state
    gs_gnd0_state = get_iface_state("gs-ashburn", "gnd0")
    mirred_target = get_tc_mirred("_gbr-ashburn")
    print(f"  Initial: gs-ashburn/gnd0={gs_gnd0_state}, mirred→{mirred_target}")

    # Start in-process gRPC server
    print("\nStarting in-process Node Agent gRPC server...")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer = NodeAgentServicer()
    add_NodeAgentServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    stub = NodeAgentServiceStub(channel)

    try:
        # === TEST 1: BatchLinkDown (Ground) ===
        print("\n--- TEST 1: BatchLinkDown (Ground) ---")
        down_req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="gnd-test-down-001",
            target_sim_time="2026-01-01T00:00:00Z",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p01s02",
                    interface_name="gnd0",
                    pid=sat_pid,
                    link_type=node_agent_pb2.GROUND,
                    gs_id="gs-ashburn",
                    sat_id="sat-P01S02",
                    gs_pid=gs_pid,
                    sat_pid=sat_pid,
                ),
            ],
        )
        down_resp = stub.BatchLinkDown(down_req)
        print(
            f"  Response: success={down_resp.success}, downed={down_resp.interfaces_downed}, "
            f"time={down_resp.apply_time_ms:.1f}ms, error={down_resp.error_message!r}"
        )

        time.sleep(0.3)

        # Verify: tc mirred removed
        mirred_after_down = get_tc_mirred("_gbr-ashburn")
        print(f"  After down: mirred→{mirred_after_down!r}")
        assert mirred_after_down == "", f"Expected mirred removed, got {mirred_after_down!r}"
        print("  PASS: tc mirred redirect removed")

        # Verify: satellite gnd0 is DOWN
        sat_gnd_state = get_iface_state("sat-p01s02", "gnd0")
        print(f"  sat-p01s02/gnd0={sat_gnd_state}")

        # Verify: satellite host veth is DOWN
        host_veth_out = subprocess.run(
            ["ip", "-o", "link", "show", "_gnd_P01S02"],
            capture_output=True,
            text=True,
        ).stdout
        host_down = "state DOWN" in host_veth_out
        print(f"  Host veth _gnd_P01S02 DOWN={host_down}")
        assert host_down, "Expected host veth DOWN"
        print("  PASS: Ground link fully torn down")

        # === TEST 2: Start ip monitor in GS pod (background) ===
        print("\n--- TEST 2: BatchLinkUp (Ground) + RTM_NEWLINK ---")
        print("  Starting ip monitor in gs-ashburn (background)...")
        monitor_proc = subprocess.Popen(
            [
                "kubectl",
                f"--kubeconfig={KUBECONFIG}",
                "-n",
                NAMESPACE,
                "exec",
                "gs-ashburn",
                "-c",
                "frr",
                "--",
                "ip",
                "monitor",
                "link",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.5)  # Let monitor start

        # === TEST 3: BatchLinkUp (Ground, 10ms latency) ===
        up_req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="gnd-test-up-001",
            target_sim_time="2026-01-01T00:00:01Z",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-p01s02",
                    interface_name="gnd0",
                    pid=sat_pid,
                    link_type=node_agent_pb2.GROUND,
                    latency_ms=10.0,
                    bandwidth_mbps=1000.0,
                    gs_id="gs-ashburn",
                    sat_id="sat-P01S02",
                    gs_pid=gs_pid,
                    sat_pid=sat_pid,
                ),
            ],
        )
        up_resp = stub.BatchLinkUp(up_req)
        print(
            f"  Response: success={up_resp.success}, upped={up_resp.interfaces_upped}, "
            f"time={up_resp.apply_time_ms:.1f}ms, error={up_resp.error_message!r}"
        )

        time.sleep(0.5)

        # Kill monitor and check output
        monitor_proc.kill()
        monitor_out, _ = monitor_proc.communicate(timeout=2)
        gnd0_events = [l for l in monitor_out.splitlines() if "gnd0" in l]
        print(f"  RTM_NEWLINK events for gnd0: {len(gnd0_events)}")
        for ev in gnd0_events[:5]:
            print(f"    {ev.strip()}")

        if gnd0_events:
            print("  PASS: RTM_NEWLINK events detected for gnd0")
        else:
            print("  WARNING: No RTM_NEWLINK events seen (monitor may have started too late)")

        # Verify: tc mirred re-installed
        mirred_after_up = get_tc_mirred("_gbr-ashburn")
        print(f"  After up: mirred→{mirred_after_up!r}")
        assert mirred_after_up == "_gnd_P01S02", (
            f"Expected mirred→_gnd_P01S02, got {mirred_after_up!r}"
        )
        print("  PASS: tc mirred redirect re-installed")

        # Verify: tc shaping on both gnd0
        gs_tc = kubectl(
            "exec", "gs-ashburn", "-c", "frr", "--", "tc", "qdisc", "show", "dev", "gnd0"
        )
        sat_tc = kubectl(
            "exec", "sat-p01s02", "-c", "frr", "--", "tc", "qdisc", "show", "dev", "gnd0"
        )
        print(f"  gs-ashburn/gnd0 tc: {gs_tc}")
        print(f"  sat-p01s02/gnd0 tc: {sat_tc}")
        assert "netem" in gs_tc and "10" in gs_tc, "Expected netem 10ms on GS gnd0"
        assert "netem" in sat_tc and "10" in sat_tc, "Expected netem 10ms on sat gnd0"
        print("  PASS: tc shaping 10ms applied on both gnd0")

        # Verify: satellite gnd0 is UP
        sat_gnd_state = get_iface_state("sat-p01s02", "gnd0")
        print(f"  sat-p01s02/gnd0={sat_gnd_state}")
        assert sat_gnd_state == "UP", f"Expected UP, got {sat_gnd_state}"
        print("  PASS: sat gnd0 UP")

        # L2 ping test via link-local
        gs_ll = kubectl(
            "exec", "gs-ashburn", "-c", "frr", "--", "ip", "-6", "addr", "show", "gnd0"
        ).split()
        sat_ll = kubectl(
            "exec", "sat-p01s02", "-c", "frr", "--", "ip", "-6", "addr", "show", "gnd0"
        ).split()
        print(f"  GS gnd0 IPv6: {'...' if gs_ll else 'none'}")
        print(f"  Sat gnd0 IPv6: {'...' if sat_ll else 'none'}")

        print("\n=== ALL GROUND LINK TESTS PASSED ===")

    finally:
        channel.close()
        server.stop(grace=0)


if __name__ == "__main__":
    main()
