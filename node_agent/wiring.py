"""Initial topology wiring — executes data plane setup from wiring manifest.

Called by the Node Agent when a new nodalarc-topology-wiring ConfigMap
is detected. Replicates na_deploy.py Step 7 using pyroute2 operations
from orchestrator/link_manager.py.

The Node Agent runs as a DaemonSet with hostPID and hostNetwork,
giving it access to all pod network namespaces on this node.
"""

from __future__ import annotations

import logging

import kubernetes.client
import kubernetes.config
from pyroute2 import IPRoute

from node_agent.link_ops import (
    _write_sysctl_in_netns,
    configure_interface,
    create_dummy_interface,
    create_ground_bridge,
    create_satellite_ground_veth,
    create_veth_pair,
    enable_mpls_input,
)
from node_agent.pid_discovery import discover_local_pod_pids

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

    # Discover PIDs for all session pods on this node.
    # Retry until ALL expected pods have non-zero PIDs. Pods may not have
    # container_statuses populated immediately after reaching Running state.
    import time

    expected_nodes = set(nodes.keys())
    pid_map: dict[str, int] = {}
    max_attempts = 30

    for attempt in range(1, max_attempts + 1):
        fresh = discover_local_pod_pids(namespace)
        pid_map.update(fresh)
        missing = expected_nodes - set(pid_map.keys())
        if not missing:
            break
        if attempt % 5 == 1:
            log.info(
                "PID discovery attempt %d: %d/%d found, %d missing",
                attempt,
                len(pid_map),
                len(expected_nodes),
                len(missing),
            )
        time.sleep(2)

    missing = expected_nodes - set(pid_map.keys())
    if missing:
        for nid in sorted(missing):
            log.warning(
                "PID=0 after %d attempts for %s — wiring will skip this pod", max_attempts, nid
            )
    log.info("PID discovery: %d/%d pods found", len(pid_map), len(expected_nodes))

    wired: dict[str, str] = {}

    # Phase 1: Set sysctls in each pod namespace (via os.setns)
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            log.warning(f"No PID for {node_id}, skipping sysctls")
            continue
        for key, value in node_spec.get("sysctls", {}).items():
            err = _write_sysctl_in_netns(pid, key, str(value))
            if err:
                log.warning(f"sysctl {key}={value} failed in {node_id}: {err}")
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
    # PRD v0.42 Section 13.6: gnd0 initial state is LOWERLAYERDOWN — FRR zebra
    # brings admin UP at config load, no carrier because host-side veth is down.
    # No explicit admin state manipulation needed. Host-side veth UP on LinkUp
    # gives gnd0 carrier automatically.
    for gs_id, _bridge_spec in ground_bridges.items():
        gs_pid = pid_map.get(gs_id, 0)
        if gs_pid == 0:
            log.warning(f"No PID for ground station {gs_id}")
            continue
        try:
            create_ground_bridge(gs_id, gs_pid)
            configure_interface(gs_pid, "gnd0", gs_id)
            enable_mpls_input(gs_pid, "gnd0")
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

    # Phase 7: Remove default route from each pod (setns + IPRoute)
    from node_agent.namespace_ops import _in_namespace

    removed = 0
    for node_id in nodes:
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        try:

            def _remove_default(ipr: IPRoute, _pid: int = pid) -> bool:
                for route in ipr.get_routes(family=2):
                    if route.get_attr("RTA_DST") is None and route["dst_len"] == 0:
                        ipr.route("del", dst="0.0.0.0/0", gateway=route.get_attr("RTA_GATEWAY"))
                        return True
                return False

            if _in_namespace(pid, _remove_default):
                removed += 1
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
