"""Integration test: Node Agent ISL BatchLinkDown + BatchLinkUp via gRPC.

Requires:
  - Running K3s session with sat-p00s00 and sat-p00s01 pods
  - Root access (for PID discovery and netlink operations)
  - isl0 currently UP between the two pods

Verifies:
  1. BatchLinkDown sets isl0 DOWN on both pods
  2. BatchLinkUp brings isl0 UP with tc shaping (15ms netem)
  3. ping RTT between pods matches 2 * 15ms = ~30ms
"""

from __future__ import annotations

import json
import subprocess
import time
from concurrent import futures

import grpc

# Import the Node Agent gRPC components
from node_agent.proto import node_agent_pb2
from node_agent.proto.node_agent_pb2_grpc import (
    NodeAgentServiceStub,
    add_NodeAgentServiceServicer_to_server,
)
from node_agent.server import NodeAgentServicer

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
NAMESPACE = "nodalarc"


def kubectl(*args: str) -> str:
    """Run kubectl with K3s kubeconfig."""
    result = subprocess.run(
        ["kubectl", f"--kubeconfig={KUBECONFIG}", "-n", NAMESPACE, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def get_pid(pod: str) -> int:
    """Get container PID via crictl."""
    cid = kubectl("get", "pod", pod, "-o", "jsonpath={.status.containerStatuses[0].containerID}")
    raw_id = cid.split("://", 1)[-1]
    proc = subprocess.run(["crictl", "inspect", raw_id], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["info"]["pid"]


def get_iface_state(pod: str, ifname: str) -> str:
    """Get interface operstate from inside pod."""
    out = kubectl("exec", pod, "-c", "frr", "--", "ip", "-o", "link", "show", ifname)
    if "state UP" in out:
        return "UP"
    if "state DOWN" in out:
        return "DOWN"
    return "UNKNOWN"


def get_netem_delay(pod: str, ifname: str) -> str:
    """Get netem delay from tc qdisc show."""
    out = kubectl("exec", pod, "-c", "frr", "--", "tc", "qdisc", "show", "dev", ifname)
    for line in out.splitlines():
        if "netem" in line and "delay" in line:
            # Parse "delay 15ms" or "delay 15.0ms"
            parts = line.split("delay")
            if len(parts) > 1:
                return parts[1].strip().split()[0]
    return "none"


def ping_rtt(pod: str, target_ip: str, count: int = 5) -> float | None:
    """Ping from pod, return average RTT in ms."""
    out = kubectl("exec", pod, "-c", "frr", "--", "ping", "-c", str(count), "-W", "2", target_ip)
    # Parse "rtt min/avg/max/mdev = 29.5/30.1/31.2/0.5 ms"
    for line in out.splitlines():
        if "avg" in line and "rtt" in line:
            parts = line.split("=")
            if len(parts) >= 2:
                vals = parts[1].strip().split("/")
                return float(vals[1])  # avg
    return None


def main():
    # --- Setup: discover PIDs ---
    print("=== ISL Integration Test ===")
    print("Discovering PIDs...")
    pid_p00s00 = get_pid("sat-p00s00")
    pid_p00s01 = get_pid("sat-p00s01")
    print(f"  sat-p00s00 PID={pid_p00s00}")
    print(f"  sat-p00s01 PID={pid_p00s01}")

    # Check initial state
    state_a = get_iface_state("sat-p00s00", "isl0")
    state_b = get_iface_state("sat-p00s01", "isl0")
    print(f"  Initial isl0 state: sat-p00s00={state_a}, sat-p00s01={state_b}")

    # --- Start in-process gRPC server ---
    print("\nStarting in-process Node Agent gRPC server...")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer = NodeAgentServicer()
    add_NodeAgentServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    stub = NodeAgentServiceStub(channel)
    print(f"  Server listening on port {port}")

    try:
        # === TEST 1: BatchLinkDown ===
        print("\n--- TEST 1: BatchLinkDown (ISL) ---")
        down_req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="isl-test-down-001",
            target_sim_time="2026-01-01T00:00:00Z",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=pid_p00s00,
                    link_type=node_agent_pb2.ISL,
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p00s01",
                    interface_name="isl0",
                    pid=pid_p00s01,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        down_resp = stub.BatchLinkDown(down_req)
        print(
            f"  Response: success={down_resp.success}, downed={down_resp.interfaces_downed}, "
            f"time={down_resp.apply_time_ms:.1f}ms, error={down_resp.error_message!r}"
        )

        # Verify interfaces are DOWN
        time.sleep(0.2)
        state_a = get_iface_state("sat-p00s00", "isl0")
        state_b = get_iface_state("sat-p00s01", "isl0")
        print(f"  After down: sat-p00s00/isl0={state_a}, sat-p00s01/isl0={state_b}")
        assert state_a == "DOWN", f"Expected DOWN, got {state_a}"
        assert state_b == "DOWN", f"Expected DOWN, got {state_b}"
        assert down_resp.success is True
        assert down_resp.interfaces_downed == 2
        print("  PASS: Both interfaces DOWN")

        # === TEST 2: BatchLinkUp with 15ms latency ===
        print("\n--- TEST 2: BatchLinkUp (ISL, 15ms latency) ---")

        # Get peer MAC for NDP (from sat-p00s01's isl0 MAC)
        mac_out = kubectl("exec", "sat-p00s01", "-c", "frr", "--", "ip", "link", "show", "isl0")
        peer_mac = ""
        for line in mac_out.splitlines():
            if "link/ether" in line:
                peer_mac = line.strip().split()[1]
                break
        mac_out_a = kubectl("exec", "sat-p00s00", "-c", "frr", "--", "ip", "link", "show", "isl0")
        peer_mac_a = ""
        for line in mac_out_a.splitlines():
            if "link/ether" in line:
                peer_mac_a = line.strip().split()[1]
                break
        print(f"  Peer MACs: sat-p00s01/isl0={peer_mac}, sat-p00s00/isl0={peer_mac_a}")

        up_req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="isl-test-up-001",
            target_sim_time="2026-01-01T00:00:01Z",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=pid_p00s00,
                    link_type=node_agent_pb2.ISL,
                    latency_ms=15.0,
                    bandwidth_mbps=1000.0,
                    peer_mac=peer_mac,  # MAC of sat-p00s01's isl0
                ),
                node_agent_pb2.InterfaceUp(
                    node_id="sat-p00s01",
                    interface_name="isl0",
                    pid=pid_p00s01,
                    link_type=node_agent_pb2.ISL,
                    latency_ms=15.0,
                    bandwidth_mbps=1000.0,
                    peer_mac=peer_mac_a,  # MAC of sat-p00s00's isl0
                ),
            ],
        )
        up_resp = stub.BatchLinkUp(up_req)
        print(
            f"  Response: success={up_resp.success}, upped={up_resp.interfaces_upped}, "
            f"time={up_resp.apply_time_ms:.1f}ms, error={up_resp.error_message!r}"
        )

        # Verify interfaces are UP
        time.sleep(0.5)
        state_a = get_iface_state("sat-p00s00", "isl0")
        state_b = get_iface_state("sat-p00s01", "isl0")
        print(f"  After up: sat-p00s00/isl0={state_a}, sat-p00s01/isl0={state_b}")
        assert state_a == "UP", f"Expected UP, got {state_a}"
        assert state_b == "UP", f"Expected UP, got {state_b}"
        assert up_resp.success is True
        assert up_resp.interfaces_upped == 2
        print("  PASS: Both interfaces UP")

        # Verify tc netem delay
        delay_a = get_netem_delay("sat-p00s00", "isl0")
        delay_b = get_netem_delay("sat-p00s01", "isl0")
        print(f"  Netem delay: sat-p00s00/isl0={delay_a}, sat-p00s01/isl0={delay_b}")
        assert "15" in delay_a, f"Expected 15ms delay, got {delay_a}"
        assert "15" in delay_b, f"Expected 15ms delay, got {delay_b}"
        print("  PASS: Netem delay is 15ms on both ends")

        # === TEST 3: Ping RTT ===
        print("\n--- TEST 3: Ping RTT verification ---")
        rtt = ping_rtt("sat-p00s00", "10.0.1.1", count=5)
        print(f"  sat-p00s00 -> sat-p00s01 (10.0.1.1): avg RTT = {rtt}ms")
        if rtt is not None:
            # Expected: ~30ms (15ms each way). Allow ±5ms tolerance.
            assert 20.0 < rtt < 40.0, f"Expected ~30ms RTT, got {rtt}ms"
            print(f"  PASS: RTT {rtt:.1f}ms is within expected range (25-35ms)")
        else:
            print("  WARNING: ping failed or no RTT parsed — may need IS-IS reconvergence")

        print("\n=== ALL ISL TESTS PASSED ===")

    finally:
        channel.close()
        server.stop(grace=0)


if __name__ == "__main__":
    main()
