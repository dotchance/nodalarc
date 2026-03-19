"""NDP timing diagnostic: fresh interfaces vs warm NDP cache.

Run with: sudo -E PYTHONPATH=... python tests/integration/ndp_timing_diag.py
"""

from __future__ import annotations

import json
import subprocess
import time
from concurrent import futures

import grpc

from node_agent.proto import node_agent_pb2
from node_agent.proto.node_agent_pb2_grpc import (
    NodeAgentServiceStub,
    add_NodeAgentServiceServicer_to_server,
)
from node_agent.server import NodeAgentServicer

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"


def get_pid(pod):
    cid = subprocess.run(
        [
            "kubectl",
            f"--kubeconfig={KUBECONFIG}",
            "-n",
            "nodalarc",
            "get",
            "pod",
            pod,
            "-o",
            "jsonpath={.status.containerStatuses[0].containerID}",
        ],
        capture_output=True,
        text=True,
    ).stdout
    raw_id = cid.split("://", 1)[-1]
    proc = subprocess.run(["crictl", "inspect", raw_id], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["info"]["pid"]


def nsenter_cmd(pid, *args):
    return subprocess.run(
        ["nsenter", "--target", str(pid), "--net", "--", *args],
        capture_output=True,
        text=True,
    ).stdout.strip()


def find_peer(pid_a, ifname_a):
    """Find the peer pod and interface for a given veth end."""
    out = nsenter_cmd(pid_a, "ip", "link", "show", ifname_a)
    peer_idx = out.split("@if")[1].split(":")[0] if "@if" in out else None
    if not peer_idx:
        return None, None, None

    # Search satellites for the peer index
    for plane in range(2):
        for slot in range(11):
            pod = f"sat-p{plane:02d}s{slot:02d}"
            try:
                pid = get_pid(pod)
                links = nsenter_cmd(pid, "ip", "link")
                for line in links.splitlines():
                    if f"{peer_idx}:" in line and "isl" in line:
                        peer_iface = line.split(":")[1].strip().split("@")[0]
                        node_id = f"sat-P{plane:02d}S{slot:02d}"
                        return pid, peer_iface, node_id
            except Exception:
                pass
    return None, None, None


def get_mac(pid, ifname):
    out = nsenter_cmd(pid, "ip", "link", "show", ifname)
    for line in out.splitlines():
        if "link/ether" in line:
            return line.strip().split()[1]
    return ""


def main():
    pid_a = get_pid("sat-p00s00")
    print(f"sat-p00s00 PID={pid_a}")

    # Find the actual veth peer for isl0
    pid_b, peer_iface, peer_node_id = find_peer(pid_a, "isl0")
    print(f"isl0 peer: {peer_node_id}/{peer_iface} PID={pid_b}")

    mac_a = get_mac(pid_a, "isl0")
    mac_b = get_mac(pid_b, peer_iface)
    print(f"MACs: sat-P00S00/isl0={mac_a}, {peer_node_id}/{peer_iface}={mac_b}")

    # Start gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    add_NodeAgentServiceServicer_to_server(NodeAgentServicer(), server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    stub = NodeAgentServiceStub(grpc.insecure_channel(f"localhost:{port}"))

    up_req = node_agent_pb2.BatchLinkUpRequest(
        batch_id="ndp-diag",
        locality=node_agent_pb2.LOCAL,
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id="sat-P00S00",
                interface_name="isl0",
                pid=pid_a,
                link_type=node_agent_pb2.ISL,
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                peer_mac=mac_b,
            ),
            node_agent_pb2.InterfaceUp(
                node_id=peer_node_id,
                interface_name=peer_iface,
                pid=pid_b,
                link_type=node_agent_pb2.ISL,
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                peer_mac=mac_a,
            ),
        ],
    )

    down_req = node_agent_pb2.BatchLinkDownRequest(
        batch_id="ndp-diag-down",
        locality=node_agent_pb2.LOCAL,
        interfaces=[
            node_agent_pb2.InterfaceDown(
                node_id="sat-P00S00",
                interface_name="isl0",
                pid=pid_a,
                link_type=node_agent_pb2.ISL,
            ),
            node_agent_pb2.InterfaceDown(
                node_id=peer_node_id,
                interface_name=peer_iface,
                pid=pid_b,
                link_type=node_agent_pb2.ISL,
            ),
        ],
    )

    # === Run 1: Fresh interfaces, no NDP cache ===
    print("\n=== Run 1: BatchLinkUp on fresh interfaces (no NDP cache) ===")
    t0 = time.monotonic()
    resp = stub.BatchLinkUp(up_req)
    elapsed = (time.monotonic() - t0) * 1000
    ndp_a = nsenter_cmd(pid_a, "ip", "-6", "neigh", "show", "dev", "isl0")
    print(f"  Time: {elapsed:.0f}ms  success={resp.success}")
    print(f"  NDP state: {ndp_a}")

    # === Run 2: Down + re-up (NDP cache may persist) ===
    print("\n=== Run 2: Down + re-up (warm NDP cache) ===")
    stub.BatchLinkDown(down_req)
    time.sleep(0.3)
    t0 = time.monotonic()
    resp = stub.BatchLinkUp(up_req)
    elapsed = (time.monotonic() - t0) * 1000
    ndp_a = nsenter_cmd(pid_a, "ip", "-6", "neigh", "show", "dev", "isl0")
    print(f"  Time: {elapsed:.0f}ms  success={resp.success}")
    print(f"  NDP state: {ndp_a}")

    # === Run 3: Down + re-up again ===
    print("\n=== Run 3: Down + re-up again ===")
    stub.BatchLinkDown(down_req)
    time.sleep(0.3)
    t0 = time.monotonic()
    resp = stub.BatchLinkUp(up_req)
    elapsed = (time.monotonic() - t0) * 1000
    ndp_a = nsenter_cmd(pid_a, "ip", "-6", "neigh", "show", "dev", "isl0")
    print(f"  Time: {elapsed:.0f}ms  success={resp.success}")
    print(f"  NDP state: {ndp_a}")

    # === Run 4: Down + re-up with explicit NDP flush ===
    print("\n=== Run 4: Down + re-up with NDP flush ===")
    stub.BatchLinkDown(down_req)
    time.sleep(0.1)
    nsenter_cmd(pid_a, "ip", "-6", "neigh", "flush", "dev", "isl0")
    nsenter_cmd(pid_b, "ip", "-6", "neigh", "flush", "dev", peer_iface)
    time.sleep(0.2)
    t0 = time.monotonic()
    resp = stub.BatchLinkUp(up_req)
    elapsed = (time.monotonic() - t0) * 1000
    ndp_a = nsenter_cmd(pid_a, "ip", "-6", "neigh", "show", "dev", "isl0")
    print(f"  Time: {elapsed:.0f}ms  success={resp.success}")
    print(f"  NDP state: {ndp_a}")

    server.stop(grace=0)


if __name__ == "__main__":
    main()
