"""Step 4A verification: PodLocationMap prints canonical node IDs mapped to agents.

Run with: sudo -E KUBECONFIG=/etc/rancher/k3s/k3s.yaml PYTHONPATH=... python tests/integration/test_pod_locator.py
"""

from __future__ import annotations

from scheduler.pod_locator import PodLocationMap


def main():
    print("=== Step 4A: Pod Location Map ===\n")

    loc = PodLocationMap()
    loc.load_from_k8s_api(namespace="nodalarc", agent_port=50100)

    print(f"Total pods: {len(loc.node_ids)}")
    print(f"Agent addresses: {loc.all_agent_addrs()}")
    print()
    print(loc.summary())

    # Verify case sensitivity: all node IDs should use canonical case
    print("\n--- Case sensitivity verification ---")
    for nid in sorted(loc.node_ids):
        if nid.startswith("sat-"):
            # sat-P01S02 format: P and S are uppercase
            suffix = nid[4:]  # P01S02
            if suffix != suffix.upper():
                print(f"  FAIL: {nid} is not canonical case (expected uppercase plane/slot)")
                return
        pid = loc.pid(nid)
        k3s = loc.k3s_node(nid)
        agent = loc.agent_addr(nid)
        print(f"  {nid:20s}  PID={pid:<10d}  node={k3s}  agent={agent}")

    print("\nPASS: All node IDs use canonical case from nodalarc.io/node-id label")


if __name__ == "__main__":
    main()
