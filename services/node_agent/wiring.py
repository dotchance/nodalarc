# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

from node_agent.ground_bridge import (
    create_ground_bridge,
    create_mediated_isl,
    create_satellite_ground_veth,
)
from node_agent.namespace_ops import (
    _in_namespace,
    _write_sysctl_in_netns,
    configure_interface,
    create_dummy_interface,
    enable_mpls_input,
)
from node_agent.pid_discovery import discover_local_pod_pids

log = logging.getLogger(__name__)


def _phase0_cleanup(pid_map: dict[str, int], nodes: dict) -> None:
    """Phase 0: Clean stale interfaces from host and pod namespaces.

    Must run synchronously BEFORE the ThreadPoolExecutor starts.
    Prevents EEXIST race conditions when 32 threads create interfaces
    concurrently on a Node Agent that restarted with stale kernel state.
    """
    # Host namespace: remove all NodalArc-managed interfaces
    ipr = IPRoute()
    try:
        host_cleaned = 0
        for link in ipr.get_links():
            ifname = link.get_attr("IFLA_IFNAME")
            if ifname and (
                ifname.startswith("_isl_")
                or ifname.startswith("_gnd_")
                or ifname.startswith("_gbr-")
                or ifname.startswith("_na_")
            ):
                try:
                    ipr.link("del", index=link["index"])
                    host_cleaned += 1
                except Exception:
                    pass
    finally:
        ipr.close()
    if host_cleaned:
        log.info(f"Phase 0: cleaned {host_cleaned} stale host interfaces")

    # Pod namespaces: remove stale isl* and gnd0 interfaces
    pod_cleaned = 0
    import contextlib

    def _clean_stale_pod_ifaces(ns_ipr: IPRoute) -> int:
        cleaned = 0
        for link in ns_ipr.get_links():
            ifname = link.get_attr("IFLA_IFNAME")
            if ifname and (ifname.startswith("isl") or ifname == "gnd0"):
                with contextlib.suppress(Exception):
                    ns_ipr.link("del", index=link["index"])
                    cleaned += 1
        return cleaned

    for _node_id, pid in pid_map.items():
        if pid == 0:
            continue
        with contextlib.suppress(Exception):
            pod_cleaned += _in_namespace(pid, _clean_stale_pod_ifaces)
    if pod_cleaned:
        log.info(f"Phase 0: cleaned {pod_cleaned} stale pod interfaces across {len(pid_map)} pods")


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

    # Discover PIDs for session pods LOCAL to this node only.
    # The wiring manifest is global (all nodes), but each Node Agent
    # only wires pods on its own K3s node. Filter expected_nodes to
    # only include pods that exist locally (NODE_NAME env var filters
    # the K8s API query in discover_local_pod_pids).
    import os
    import time

    local_node = os.environ.get("NODE_NAME", "")
    all_manifest_nodes = set(nodes.keys())
    pid_map: dict[str, int] = {}
    max_attempts = 30

    for attempt in range(1, max_attempts + 1):
        fresh = discover_local_pod_pids(namespace)
        pid_map.update(fresh)
        # Expected = local pods that are in the manifest
        expected_nodes = all_manifest_nodes & set(pid_map.keys())
        # Also check if we've stabilized (no new PIDs found in 2 consecutive attempts)
        if attempt >= 3 and len(pid_map) > 0:
            # All locally discoverable pods found — stop waiting
            prev_count = len(expected_nodes)
            if prev_count == len(pid_map):
                break
        if attempt % 5 == 1:
            log.info(
                "PID discovery attempt %d: %d local pods found (manifest has %d total, node=%s)",
                attempt,
                len(pid_map),
                len(all_manifest_nodes),
                local_node,
            )
        time.sleep(2)

    expected_nodes = all_manifest_nodes & set(pid_map.keys())
    missing = expected_nodes - set(pid_map.keys())
    remote_nodes = all_manifest_nodes - expected_nodes
    if remote_nodes:
        log.info(
            "%d pods on other nodes (not wired locally): %s",
            len(remote_nodes),
            ", ".join(sorted(remote_nodes)[:5]) + ("..." if len(remote_nodes) > 5 else ""),
        )
    if missing:
        for nid in sorted(missing):
            log.warning(
                "PID=0 after %d attempts for %s — wiring will skip this pod", max_attempts, nid
            )
    log.info("PID discovery: %d/%d pods found", len(pid_map), len(expected_nodes))

    wired: dict[str, str] = {}
    total_nodes = len([n for n in nodes if pid_map.get(n, 0) > 0])

    def _write_progress(phase_msg: str) -> None:
        """Write incremental wiring progress to ConfigMap so the Operator can surface it."""
        try:
            kubernetes.config.load_incluster_config()
            v1 = kubernetes.client.CoreV1Api()
            body = kubernetes.client.V1ConfigMap(
                metadata=kubernetes.client.V1ObjectMeta(
                    name="nodalarc-wiring-status",
                    namespace=namespace,
                    labels={"nodalarc.io/managed-by": "node-agent"},
                ),
                data={"_progress": phase_msg},
            )
            try:
                v1.create_namespaced_config_map(namespace, body)
            except kubernetes.client.rest.ApiException as e:
                if e.status == 409:
                    existing = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
                    if existing.data is None:
                        existing.data = {}
                    existing.data["_progress"] = phase_msg
                    v1.replace_namespaced_config_map("nodalarc-wiring-status", namespace, existing)
        except Exception:
            pass  # Non-fatal — progress reporting failure shouldn't block wiring

    # Phase 0: Clean stale interfaces from host and pod namespaces.
    # Must run BEFORE the ThreadPoolExecutor starts creating interfaces.
    # Without this, 32 concurrent threads racing to create and clean
    # interfaces produce EEXIST race conditions.
    _write_progress(f"Cleaning stale interfaces for {total_nodes} nodes")
    _phase0_cleanup(pid_map, nodes)

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
    _write_progress(f"Sysctls configured for {total_nodes} nodes. Creating ISL interfaces...")

    # Phase 2: Create ISL veth pairs (deduplicate A→B and B→A, parallelized)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    isl_tasks: list[tuple[int, int, str, str, str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for node_id, node_spec in nodes.items():
        pid_a = pid_map.get(node_id, 0)
        if pid_a == 0:
            continue
        for iface in node_spec.get("isl_interfaces", []):
            peer_node = iface["peer_node"]
            pair = (min(node_id, peer_node), max(node_id, peer_node))
            if pair in seen_pairs:
                continue
            pid_b = pid_map.get(peer_node, 0)
            if pid_b == 0:
                log.warning(f"No PID for peer {peer_node}, skipping ISL {node_id}<->{peer_node}")
                continue
            peer_iface = iface.get("peer_iface", "")
            if not peer_iface:
                log.warning(f"No peer_iface for {node_id}:{iface['name']}<->{peer_node}")
                continue
            isl_tasks.append((pid_a, pid_b, iface["name"], peer_iface, node_id, peer_node))
            seen_pairs.add(pair)

    created_links: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = {}
        for pid_a, pid_b, ifname_a, ifname_b, nid_a, nid_b in isl_tasks:
            fut = pool.submit(
                create_mediated_isl,
                pid_a,
                pid_b,
                ifname_a,
                ifname_b,
                node_id_a=nid_a,
                node_id_b=nid_b,
            )
            futures[fut] = (nid_a, nid_b)
        for fut in as_completed(futures):
            nid_a, nid_b = futures[fut]
            try:
                fut.result()
                created_links.add((min(nid_a, nid_b), max(nid_a, nid_b)))
            except Exception as exc:
                log.warning(f"Failed to create mediated ISL {nid_a}<->{nid_b}: {exc}")
    log.info(f"Phase 2: created {len(created_links)} host-mediated ISL pairs")
    _write_progress(f"Created {len(created_links)} ISL pairs. Enabling MPLS...")

    # Phase 3: Enable MPLS input on ISL interfaces (parallelized)
    mpls_tasks = []
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0 or not node_spec.get("mpls_enable"):
            continue
        for iface in node_spec.get("isl_interfaces", []):
            mpls_tasks.append((pid, iface["name"], node_id))

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = {
            pool.submit(enable_mpls_input, pid, ifname): (nid, ifname)
            for pid, ifname, nid in mpls_tasks
        }
        for fut in as_completed(futures):
            nid, ifname = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.warning(f"MPLS enable failed {nid}:{ifname}: {exc}")
    log.info(f"Phase 3: MPLS input enabled on {len(mpls_tasks)} ISL interfaces")
    _write_progress(
        f"MPLS enabled on {len(mpls_tasks)} interfaces. Creating ground infrastructure..."
    )

    # Phase 4+5: Create ground infrastructure (parallelized)
    # Ground bridges (GS-side) and satellite ground veths are independent
    # and can be created concurrently. gnd0 starts admin DOWN; FRR zebra
    # brings it admin UP (no `shutdown` in config). With no host-side veth
    # connected, gnd0 enters LOWERLAYERDOWN (admin UP, no carrier).

    def _create_ground_bridge_task(gs_id: str, gs_pid: int) -> None:
        create_ground_bridge(gs_id, gs_pid)
        configure_interface(gs_pid, "gnd0", gs_id)
        enable_mpls_input(gs_pid, "gnd0")

    def _create_sat_ground_task(node_id: str, pid: int) -> None:
        create_satellite_ground_veth(node_id, pid)
        configure_interface(pid, "gnd0", node_id)
        enable_mpls_input(pid, "gnd0")

    with ThreadPoolExecutor(max_workers=32) as pool:
        gnd_futures = {}
        for gs_id, _bridge_spec in ground_bridges.items():
            gs_pid = pid_map.get(gs_id, 0)
            if gs_pid == 0:
                log.warning(f"No PID for ground station {gs_id}")
                continue
            gnd_futures[pool.submit(_create_ground_bridge_task, gs_id, gs_pid)] = gs_id

        for node_id, node_spec in nodes.items():
            if node_spec.get("node_type") != "satellite":
                continue
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            gnd_futures[pool.submit(_create_sat_ground_task, node_id, pid)] = node_id

        gs_created = 0
        sat_gnd_created = 0
        for fut in as_completed(gnd_futures):
            nid = gnd_futures[fut]
            try:
                fut.result()
                if nid.startswith("gs-"):
                    gs_created += 1
                else:
                    sat_gnd_created += 1
            except Exception as exc:
                log.warning(f"Ground setup failed for {nid}: {exc}")
    log.info(f"Phase 4+5: {gs_created} ground bridges, {sat_gnd_created} satellite ground veths")
    _write_progress(
        f"Ground infrastructure ready: {gs_created} GS, {sat_gnd_created} satellites. Creating terrestrial interfaces..."
    )

    # Phase 6: Create terr0 dummy interfaces for ground stations (parallelized)
    terr0_tasks = []
    for node_id, node_spec in nodes.items():
        if node_spec.get("node_type") != "ground_station":
            continue
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            continue
        addrs = node_spec.get("terrestrial", {}).get("addresses", [])
        if addrs:
            terr0_tasks.append((pid, node_id, addrs))

    with ThreadPoolExecutor(max_workers=32) as pool:
        terr_futures = {
            pool.submit(create_dummy_interface, pid, "terr0", addrs): nid
            for pid, nid, addrs in terr0_tasks
        }
        for fut in as_completed(terr_futures):
            nid = terr_futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.warning(f"terr0 creation failed for {nid}: {exc}")
    log.info(f"Phase 6: {len(terr0_tasks)} terr0 dummy interfaces created")
    _write_progress(
        f"Terrestrial interfaces created. Finalizing {total_nodes} pods (routes + security)..."
    )

    # Phase 7+8: Per-pod finalization (parallelized)
    # Default route removal + cni0 iptables lockdown per pod.
    import subprocess

    from node_agent.namespace_ops import _in_namespace

    def _finalize_pod(node_id: str, pid: int) -> str | None:
        """Remove default route and lock down cni0. Returns error or None."""
        try:
            # Phase 7: Remove default route
            def _remove_default(ipr: IPRoute) -> bool:
                for route in ipr.get_routes(family=2):
                    if route.get_attr("RTA_DST") is None and route["dst_len"] == 0:
                        ipr.route("del", dst="0.0.0.0/0", gateway=route.get_attr("RTA_GATEWAY"))
                        return True
                return False

            _in_namespace(pid, _remove_default)

            # Phase 8: iptables cni0 lockdown
            ns_path = f"/proc/{pid}/ns/net"
            for cmd in [
                [
                    "nsenter",
                    f"--net={ns_path}",
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-o",
                    "cni0",
                    "-m",
                    "state",
                    "--state",
                    "ESTABLISHED,RELATED",
                    "-j",
                    "ACCEPT",
                ],
                [
                    "nsenter",
                    f"--net={ns_path}",
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-o",
                    "cni0",
                    "-j",
                    "DROP",
                ],
            ]:
                subprocess.run(cmd, check=True, capture_output=True)
            return None
        except Exception as exc:
            return f"{node_id}: {exc}"

    finalized = 0
    with ThreadPoolExecutor(max_workers=32) as pool:
        fin_futures = {}
        for node_id in nodes:
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            fin_futures[pool.submit(_finalize_pod, node_id, pid)] = node_id
        for fut in as_completed(fin_futures):
            nid = fin_futures[fut]
            try:
                err = fut.result()
                if err:
                    log.warning(f"Pod finalization failed: {err}")
                else:
                    finalized += 1
            except Exception as exc:
                log.warning(f"Pod finalization error for {nid}: {exc}")
    log.info(f"Phase 7+8: finalized {finalized} pods (default route + cni0 lockdown)")
    _write_progress(f"Finalized {finalized}/{total_nodes} pods. Wiring complete.")

    # Mark all nodes as wired
    for node_id in nodes:
        if pid_map.get(node_id, 0) > 0:
            wired[node_id] = "wired"

    log.info(f"Wiring complete: {len(wired)}/{len(nodes)} nodes wired")
    return wired


def write_wiring_status(wired: dict[str, str], namespace: str = "nodalarc") -> None:
    """Write per-node wiring status to nodalarc-wiring-status ConfigMap.

    Uses PATCH (merge) so multiple Node Agents on different K3s nodes
    can each write their local pods without overwriting each other.
    """
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
            # ConfigMap exists — read existing, merge, update
            existing = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
            merged = dict(existing.data or {})
            merged.update(wired)
            existing.data = merged
            v1.replace_namespaced_config_map("nodalarc-wiring-status", namespace, existing)
        else:
            raise
    log.info(f"Wrote wiring status: {len(wired)} nodes wired")
