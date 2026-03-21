"""Initial topology wiring — executes data plane setup from wiring manifest.

Called by the Node Agent when a new nodalarc-topology-wiring ConfigMap
is detected. Replicates na_deploy.py Step 7 using pyroute2 operations
from orchestrator/link_manager.py.

The Node Agent runs as a DaemonSet with hostPID and hostNetwork,
giving it access to all pod network namespaces on this node.
"""

from __future__ import annotations

import logging
from pathlib import Path

import kubernetes.client
import kubernetes.config
from pyroute2 import NetNS

from node_agent.pid_discovery import discover_local_pod_pids
from orchestrator.link_manager import (
    configure_interface,
    create_dummy_interface,
    create_ground_bridge,
    create_satellite_ground_veth,
    create_veth_pair,
    enable_mpls_input,
    set_interface_up,
)

log = logging.getLogger(__name__)


def execute_wiring(manifest: dict, namespace: str = "nodalarc") -> dict[str, str]:
    """Execute all data plane wiring operations from a topology manifest.

    Args:
        manifest: Parsed wiring manifest from ConfigMap.
        namespace: K8s namespace for pod discovery.

    Returns:
        Dict of {node_id: "wired"} for successfully wired nodes.
    """
    nodes = manifest.get("nodes", {})
    ground_bridges = manifest.get("ground_bridges", {})

    if not nodes:
        log.warning("Empty wiring manifest — nothing to wire")
        return {}

    # Discover PIDs for all session pods on this node
    pid_map = discover_local_pod_pids(namespace)
    if not pid_map:
        # Retry once after 3 seconds
        import time

        time.sleep(3)
        pid_map = discover_local_pod_pids(namespace)
    log.info(f"PID discovery: {len(pid_map)} pods found")

    wired: dict[str, str] = {}

    # Phase 1: Set sysctls in each pod namespace (via /proc/{pid}/root/proc/sys/)
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            log.warning(f"No PID for {node_id}, skipping sysctls")
            continue
        for key, value in node_spec.get("sysctls", {}).items():
            # net.ipv6.conf.all.forwarding → /proc/{pid}/root/proc/sys/net/ipv6/conf/all/forwarding
            sysctl_path = Path(f"/proc/{pid}/root/proc/sys") / key.replace(".", "/")
            try:
                sysctl_path.write_text(str(value))
            except OSError as exc:
                log.warning(f"sysctl {key}={value} failed in {node_id}: {exc}")
    log.info(f"Phase 1: sysctls set for {len(nodes)} nodes")

    # Phase 2: Create ISL veth pairs (deduplicate A→B and B→A)
    created_links: set[tuple[str, str]] = set()
    for node_id, node_spec in nodes.items():
        pid_a = pid_map.get(node_id, 0)
        if pid_a == 0:
            continue
        for iface in node_spec.get("isl_interfaces", []):
            peer_node = iface["peer_node"]
            pair = (min(node_id, peer_node), max(node_id, peer_node))
            if pair in created_links:
                continue
            pid_b = pid_map.get(peer_node, 0)
            if pid_b == 0:
                log.warning(f"No PID for peer {peer_node}, skipping ISL {node_id}<->{peer_node}")
                continue
            peer_iface = iface.get("peer_iface", "")
            if not peer_iface:
                log.warning(f"No peer_iface for {node_id}:{iface['name']}<->{peer_node}")
                continue
            try:
                create_veth_pair(
                    pid_a,
                    pid_b,
                    iface["name"],
                    peer_iface,
                    node_id_a=node_id,
                    node_id_b=peer_node,
                )
                created_links.add(pair)
            except Exception as exc:
                log.warning(f"Failed to create veth {node_id}<->{peer_node}: {exc}")
    log.info(f"Phase 2: created {len(created_links)} ISL veth pairs")

    # Phase 3: Enable MPLS input on ISL interfaces
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        if not node_spec.get("mpls_enable"):
            continue
        for iface in node_spec.get("isl_interfaces", []):
            try:
                enable_mpls_input(pid, iface["name"])
            except Exception as exc:
                log.warning(f"MPLS enable failed {node_id}:{iface['name']}: {exc}")
    log.info("Phase 3: MPLS input enabled on ISL interfaces")

    # Phase 4: Create ground bridges and GS gnd0 interfaces
    for gs_id, _bridge_spec in ground_bridges.items():
        gs_pid = pid_map.get(gs_id, 0)
        if gs_pid == 0:
            log.warning(f"No PID for ground station {gs_id}")
            continue
        try:
            create_ground_bridge(gs_id, gs_pid)
            configure_interface(gs_pid, "gnd0", gs_id)
            enable_mpls_input(gs_pid, "gnd0")
            set_interface_up(gs_pid, "gnd0")
        except Exception as exc:
            log.warning(f"Ground bridge setup failed for {gs_id}: {exc}")
    log.info(f"Phase 4: created {len(ground_bridges)} ground bridges")

    # Phase 5: Create satellite ground veths (all start admin DOWN)
    for node_id, node_spec in nodes.items():
        if node_spec.get("node_type") != "satellite":
            continue
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        try:
            create_satellite_ground_veth(node_id, pid)
            configure_interface(pid, "gnd0", node_id)
            enable_mpls_input(pid, "gnd0")
        except Exception as exc:
            log.warning(f"Satellite ground veth failed for {node_id}: {exc}")
    log.info("Phase 5: satellite ground veths created")

    # Phase 6: Create terr0 dummy interfaces for ground stations
    for node_id, node_spec in nodes.items():
        if node_spec.get("node_type") != "ground_station":
            continue
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        addrs = node_spec.get("terrestrial", {}).get("addresses", [])
        if addrs:
            try:
                create_dummy_interface(pid, "terr0", addrs)
            except Exception as exc:
                log.warning(f"terr0 creation failed for {node_id}: {exc}")
    log.info("Phase 6: terr0 dummy interfaces created")

    # Phase 7: Remove default route from each pod (pyroute2)
    removed = 0
    for node_id in nodes:
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        try:
            ns = NetNS(f"/proc/{pid}/ns/net")
            try:
                # Get all IPv4 routes with dst_len=0 (default routes)
                for route in ns.get_routes(family=2):
                    if route.get_attr("RTA_DST") is None and route["dst_len"] == 0:
                        ns.route("del", dst="0.0.0.0/0", gateway=route.get_attr("RTA_GATEWAY"))
                        removed += 1
                        break
            finally:
                ns.close()
        except Exception as exc:
            log.warning(f"Default route removal failed for {node_id}: {exc}")
    log.info(f"Phase 7: removed default route from {removed} pods")

    # Mark all nodes as wired
    for node_id in nodes:
        if pid_map.get(node_id, 0) > 0:
            wired[node_id] = "wired"

    log.info(f"Wiring complete: {len(wired)}/{len(nodes)} nodes wired")
    return wired


def write_wiring_status(wired: dict[str, str], namespace: str = "nodalarc") -> None:
    """Write per-node wiring status to nodalarc-wiring-status ConfigMap."""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    body = kubernetes.client.V1ConfigMap(
        metadata=kubernetes.client.V1ObjectMeta(
            name="nodalarc-wiring-status",
            namespace=namespace,
            labels={"nodalarc.io/managed-by": "node-agent"},
        ),
        data=wired,
    )
    try:
        v1.create_namespaced_config_map(namespace, body)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:
            v1.replace_namespaced_config_map("nodalarc-wiring-status", namespace, body)
        else:
            raise
    log.info(f"Wrote wiring status: {len(wired)} nodes wired")
