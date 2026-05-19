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

import contextlib
import logging
from collections.abc import Callable
from typing import Any

import kubernetes.client
import kubernetes.config
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.wiring_status import (
    NodeWiringStatus,
    failed_status,
    ready_status,
    status_configmap_data,
)
from pydantic import ValidationError
from pyroute2 import IPRoute

from node_agent.ground_bridge import (
    create_ground_bridge,
    create_mediated_isl,
    create_satellite_ground_veth,
)
from node_agent.mpls import load_mpls_kernel_modules
from node_agent.namespace_ops import (
    _in_namespace,
    _write_sysctl_in_netns,
    configure_interface,
    create_dummy_interface,
    enable_mpls_input,
)
from node_agent.pid_discovery import discover_local_pod_pids

_IPTABLES_RULES = (
    "*filter\n"
    "-A OUTPUT -o cni0 -m state --state ESTABLISHED,RELATED -j ACCEPT\n"
    "-A OUTPUT -o cni0 -j DROP\n"
    "COMMIT\n"
)


def remove_default_route(pid: int, node_id: str) -> str | None:
    """Remove the pod default IPv4 route. Returns error string or None."""
    try:

        def _remove_default(ipr: IPRoute) -> bool:
            for route in ipr.get_routes(family=2):
                if route.get_attr("RTA_DST") is None and route["dst_len"] == 0:
                    ipr.route("del", dst="0.0.0.0/0", gateway=route.get_attr("RTA_GATEWAY"))
                    return True
            return False

        _in_namespace(pid, _remove_default)
        return None
    except Exception as exc:
        return f"{node_id}: {exc}"


def lock_down_cni0(pid: int, node_id: str) -> str | None:
    """Apply cni0 egress lockdown rules. Returns error string or None."""
    import subprocess

    try:
        subprocess.run(
            ["nsenter", f"--net=/proc/{pid}/ns/net", "iptables-restore", "--noflush"],
            input=_IPTABLES_RULES,
            text=True,
            check=True,
            capture_output=True,
        )
        return None
    except Exception as exc:
        return f"{node_id}: {exc}"


def finalize_pod_phases(pid: int, node_id: str) -> tuple[str | None, str | None]:
    """Run Phase 7 route finalization and Phase 8 cni0 security."""
    route_err = remove_default_route(pid, node_id)
    security_err = lock_down_cni0(pid, node_id)
    return route_err, security_err


def finalize_pod(pid: int, node_id: str) -> str | None:
    """Remove default route and lock down cni0. Returns combined error or None."""
    errors = [err for err in finalize_pod_phases(pid, node_id) if err]
    return "; ".join(errors) if errors else None


log = logging.getLogger(__name__)


def _phase0_cleanup(
    pid_map: dict[str, int],
    nodes: dict,
    progress_fn: Callable[[str], None] | None = None,
) -> None:
    """Phase 0: Clean stale interfaces from host and pod namespaces.

    Must run synchronously BEFORE the ThreadPoolExecutor starts.
    Prevents EEXIST race conditions when 32 threads create interfaces
    concurrently on a Node Agent that restarted with stale kernel state.
    """
    # Host namespace: remove all NodalArc-managed interfaces
    if progress_fn:
        progress_fn(f"Cleaning stale interfaces for {len(pid_map)} pods")
    ipr = IPRoute()
    try:
        host_cleaned = 0
        for link in ipr.get_links():
            ifname = link.get_attr("IFLA_IFNAME")
            if ifname and (
                ifname.startswith("_isl_")
                or ifname.startswith("_gnd_")
                or ifname.startswith("_gbr-")
                or (ifname.startswith("_g") and len(ifname) > 2 and ifname[2:3].isdigit())
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
        log.info("Phase 0: cleaned %d stale host interfaces", host_cleaned)

    # Pod namespaces: remove stale isl* and gnd0 interfaces
    pod_cleaned = 0
    import contextlib

    def _clean_stale_pod_ifaces(ns_ipr: IPRoute) -> int:
        cleaned = 0
        for link in ns_ipr.get_links():
            ifname = link.get_attr("IFLA_IFNAME")
            if ifname and (
                ifname.startswith("isl")
                or ifname.startswith("term")
                or ifname.startswith("gnd")
                or ifname.startswith("terr")
            ):
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
        log.info(
            "Phase 0: cleaned %d stale pod interfaces across %d pods",
            pod_cleaned,
            len(pid_map),
        )


def execute_wiring(
    manifest: dict[str, Any] | WiringManifest,
    namespace: str,
    progress_fn: Callable[[str], None] | None = None,
) -> dict[str, NodeWiringStatus]:
    """Execute all data plane wiring operations from a topology manifest.

    Args:
        manifest: Parsed wiring manifest from ConfigMap.
        namespace: K8s namespace for pod discovery.
        progress_fn: Optional callback for real-time progress via NATS.

    Returns:
        Dict of {node_id: NodeWiringStatus} for local pods that completed or
        failed required wiring phases.
    """
    try:
        manifest_model = (
            manifest
            if isinstance(manifest, WiringManifest)
            else WiringManifest.model_validate(manifest)
        )
    except ValidationError:
        log.exception("Wiring manifest validation failed")
        raise

    nodes = {
        node_id: node.model_dump(exclude_none=True)
        for node_id, node in manifest_model.nodes.items()
    }
    ground_bridges = manifest_model.ground_bridges

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
        if attempt == 1 and not pid_map:
            break
        expected_nodes = all_manifest_nodes & set(pid_map.keys())
        if attempt >= 3 and len(pid_map) > 0:
            prev_count = len(expected_nodes)
            if prev_count == len(pid_map):
                break
        if attempt % 5 == 1:
            log.debug(
                "PID discovery attempt %d: %d local pods found (manifest has %d total, node=%s)",
                attempt,
                len(pid_map),
                len(all_manifest_nodes),
                local_node,
            )
        time.sleep(2)

    if not pid_map:
        log.info("No local session pods on this node — nothing to wire")
        return {}

    expected_nodes = all_manifest_nodes & set(pid_map.keys())
    remote_nodes = all_manifest_nodes - set(pid_map.keys())
    if remote_nodes:
        log.debug(
            "%d pods on other nodes (not wired locally)",
            len(remote_nodes),
        )
    log.info("PID discovery: %d local pods found", len(pid_map))

    statuses: dict[str, NodeWiringStatus] = {}
    node_failures: dict[str, tuple[str, str]] = {}
    total_nodes = len([n for n in nodes if pid_map.get(n, 0) > 0])

    def _record_failure(node_id: str, phase: str, message: str) -> None:
        node_failures.setdefault(node_id, (phase, message))
        log.warning("%s failed for %s: %s", phase, node_id, message)

    # K8s client — ONE instance, reused for all ConfigMap writes.
    # No per-call load_incluster_config() or client instantiation.
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    def _write_progress(phase_msg: str) -> None:
        """Publish wiring progress via NATS (fast) and K8s ConfigMap (fallback)."""
        # NATS fast path (<1ms to VS-API)
        if progress_fn is not None:
            with contextlib.suppress(Exception):
                progress_fn(phase_msg)
        # K8s PATCH fallback (for Operator CR status updates)
        try:
            v1.patch_namespaced_config_map(
                "nodalarc-wiring-status",
                namespace,
                {"data": {"_progress": phase_msg}},
            )
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                body = kubernetes.client.V1ConfigMap(
                    metadata=kubernetes.client.V1ObjectMeta(
                        name="nodalarc-wiring-status",
                        namespace=namespace,
                        labels={"nodalarc.io/managed-by": "node-agent"},
                    ),
                    data={"_progress": phase_msg},
                )
                with contextlib.suppress(Exception):
                    v1.create_namespaced_config_map(namespace, body)
        except Exception:
            pass  # Non-fatal

    # Phase 0: Clean stale interfaces from host and pod namespaces.
    # Must run BEFORE the ThreadPoolExecutor starts creating interfaces.
    # Without this, 8 concurrent threads racing to create and clean
    # interfaces produce EEXIST race conditions.
    _write_progress(f"Cleaning stale interfaces for {total_nodes} nodes")
    _phase0_cleanup(pid_map, nodes, progress_fn=progress_fn)

    # Phase 1: Set sysctls in each pod namespace (via os.setns)
    sysctl_ok = 0
    sysctl_skipped = []
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            sysctl_skipped.append(node_id)
            continue
        for key, value in node_spec.get("sysctls", {}).items():
            err = _write_sysctl_in_netns(pid, key, str(value))
            if err:
                _record_failure(node_id, "sysctls", f"sysctl {key}={value} failed: {err}")
        sysctl_ok += 1
    if sysctl_skipped:
        log.warning(
            "Sysctls: %d applied, %d skipped (no PID yet): %s",
            sysctl_ok,
            len(sysctl_skipped),
            ", ".join(sysctl_skipped),
        )
    else:
        log.info("Sysctls applied to all %d nodes", sysctl_ok)
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
                log.warning(
                    "No PID for peer %s, skipping ISL %s<->%s",
                    peer_node,
                    node_id,
                    peer_node,
                )
                continue
            peer_iface = iface.get("peer_iface", "")
            if not peer_iface:
                log.warning(
                    "No peer_iface for %s:%s<->%s",
                    node_id,
                    iface["name"],
                    peer_node,
                )
                continue
            isl_tasks.append((pid_a, pid_b, iface["name"], peer_iface, node_id, peer_node))
            seen_pairs.add(pair)

    created_links: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
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
        total_isls = len(futures)
        for fut in as_completed(futures):
            nid_a, nid_b = futures[fut]
            try:
                fut.result()
                created_links.add((min(nid_a, nid_b), max(nid_a, nid_b)))
                if len(created_links) % 25 == 0 or len(created_links) == total_isls:
                    _write_progress(
                        f"Creating ISL interfaces: {len(created_links)}/{total_isls} pairs"
                    )
            except Exception as exc:
                _record_failure(nid_a, "isl_interfaces", f"mediated ISL to {nid_b}: {exc}")
                _record_failure(nid_b, "isl_interfaces", f"mediated ISL to {nid_a}: {exc}")
    log.info("Phase 2: created %d host-mediated ISL pairs", len(created_links))
    requires_mpls = any(bool(node_spec.get("mpls_enable")) for node_spec in nodes.values())
    if requires_mpls:
        load_mpls_kernel_modules()
        _write_progress(f"Created {len(created_links)} ISL pairs. Enabling MPLS...")
    else:
        _write_progress(f"Created {len(created_links)} ISL pairs. MPLS not requested.")

    # Phase 3: Enable MPLS input on ISL interfaces (parallelized)
    mpls_tasks = []
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0 or not node_spec.get("mpls_enable"):
            continue
        for iface in node_spec.get("isl_interfaces", []):
            mpls_tasks.append((pid, iface["name"], node_id))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(enable_mpls_input, pid, ifname): (nid, ifname)
            for pid, ifname, nid in mpls_tasks
        }
        for fut in as_completed(futures):
            nid, ifname = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                _record_failure(nid, "mpls", f"MPLS enable failed {ifname}: {exc}")
    log.info("Phase 3: MPLS input enabled on %d ISL interfaces", len(mpls_tasks))
    if requires_mpls:
        _write_progress(
            f"MPLS enabled on {len(mpls_tasks)} interfaces. Creating ground infrastructure..."
        )
    else:
        _write_progress("Creating ground infrastructure...")

    # Phase 4+5: Create ground infrastructure (parallelized)
    # Ground bridges (GS-side) and satellite ground veths are independent
    # and can be created concurrently. gnd0 starts admin DOWN; FRR zebra
    # brings it admin UP (no `shutdown` in config). With no host-side veth
    # connected, gnd0 enters LOWERLAYERDOWN (admin UP, no carrier).

    def _create_ground_bridge_task(gs_id: str, gs_pid: int, gnd_ifaces: list, mpls: bool) -> None:
        for iface_spec in gnd_ifaces:
            ifname = iface_spec["name"]
            create_ground_bridge(gs_id, gs_pid, ifname=ifname)
            configure_interface(gs_pid, ifname, gs_id)
            if mpls:
                enable_mpls_input(gs_pid, ifname)

    def _create_sat_ground_task(node_id: str, pid: int, gnd_ifaces: list, mpls: bool) -> None:
        for iface_spec in gnd_ifaces:
            ifname = iface_spec["name"]
            create_satellite_ground_veth(node_id, pid, ifname=ifname)
            configure_interface(pid, ifname, node_id)
            if mpls:
                enable_mpls_input(pid, ifname)

    with ThreadPoolExecutor(max_workers=8) as pool:
        gnd_futures = {}
        for gs_id, _bridge_spec in ground_bridges.items():
            gs_pid = pid_map.get(gs_id, 0)
            if gs_pid == 0:
                log.warning("No PID for ground station %s", gs_id)
                continue
            gs_node = nodes.get(gs_id, {})
            gs_ifaces = gs_node["gnd_interfaces"]
            gs_mpls = gs_node.get("mpls_enable", False)
            gnd_futures[
                pool.submit(_create_ground_bridge_task, gs_id, gs_pid, gs_ifaces, gs_mpls)
            ] = gs_id

        for node_id, node_spec in nodes.items():
            if node_spec.get("node_type") != "satellite":
                continue
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            sat_ifaces = node_spec["gnd_interfaces"]
            sat_mpls = node_spec.get("mpls_enable", False)
            gnd_futures[
                pool.submit(_create_sat_ground_task, node_id, pid, sat_ifaces, sat_mpls)
            ] = node_id

        gs_created = 0
        sat_gnd_created = 0
        for fut in as_completed(gnd_futures):
            nid = gnd_futures[fut]
            try:
                fut.result()
                if nid in ground_bridges:
                    gs_created += 1
                else:
                    sat_gnd_created += 1
            except Exception as exc:
                _record_failure(nid, "ground_infrastructure", str(exc))
    log.info(
        "Phase 4+5: %d ground bridges, %d satellite ground veths",
        gs_created,
        sat_gnd_created,
    )
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

    with ThreadPoolExecutor(max_workers=8) as pool:
        terr_futures = {
            pool.submit(create_dummy_interface, pid, "terr0", addrs): nid
            for pid, nid, addrs in terr0_tasks
        }
        for fut in as_completed(terr_futures):
            nid = terr_futures[fut]
            try:
                fut.result()
            except Exception as exc:
                _record_failure(nid, "terrestrial_interfaces", f"terr0 creation failed: {exc}")
    log.info("Phase 6: %d terr0 dummy interfaces created", len(terr0_tasks))
    _write_progress(
        f"Terrestrial interfaces created. Finalizing {total_nodes} pods (routes + security)..."
    )

    # Phase 7+8: Per-pod finalization — default route removal + cni0 lockdown.
    finalized = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fin_futures = {}
        for node_id in nodes:
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            fin_futures[pool.submit(finalize_pod_phases, pid, node_id)] = node_id
        total_to_finalize = len(fin_futures)
        for fut in as_completed(fin_futures):
            nid = fin_futures[fut]
            try:
                route_err, security_err = fut.result()
                if route_err:
                    _record_failure(nid, "pod_route_finalization", route_err)
                if security_err:
                    _record_failure(nid, "pod_security", security_err)
                if not route_err and not security_err:
                    finalized += 1
                if finalized % 10 == 0 or finalized == total_to_finalize:
                    _write_progress(
                        f"Finalizing pods: {finalized}/{total_to_finalize} (default route removal)"
                    )
            except Exception as exc:
                _record_failure(nid, "pod_security", str(exc))
    log.info("Phase 7+8: finalized %d pods (default route + cni0 lockdown)", finalized)
    _write_progress(f"Finalized {finalized}/{total_nodes} pods. Wiring complete.")

    # Mark only nodes with all required phases successful as ready.
    for node_id in nodes:
        if pid_map.get(node_id, 0) > 0:
            if node_id in node_failures:
                phase, message = node_failures[node_id]
                statuses[node_id] = failed_status(
                    node_id,
                    manifest_model,
                    phase=phase,
                    error_message=message,
                    dirty_kernel=True,
                )
            else:
                statuses[node_id] = ready_status(node_id, manifest_model)

    ready_count = sum(1 for status in statuses.values() if status.status == "ready")
    failed_count = sum(1 for status in statuses.values() if status.status != "ready")
    log.info(
        "Wiring complete: %d ready, %d failed, %d manifest nodes",
        ready_count,
        failed_count,
        len(nodes),
    )
    return statuses


def write_wiring_status(
    statuses: dict[str, NodeWiringStatus],
    manifest: WiringManifest,
    namespace: str,
) -> None:
    """Write per-node wiring status to nodalarc-wiring-status ConfigMap.

    Uses JSON Merge Patch (application/merge-patch+json) so multiple
    Node Agents on different K3s nodes can each write their local pods
    without overwriting each other. Each agent sends only its delta
    (the nodes it wired), and K8s merges into the existing data.
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    try:
        v1.patch_namespaced_config_map(
            "nodalarc-wiring-status",
            namespace,
            {"data": status_configmap_data(statuses, manifest)},
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            # ConfigMap doesn't exist — create it
            body = kubernetes.client.V1ConfigMap(
                metadata=kubernetes.client.V1ObjectMeta(
                    name="nodalarc-wiring-status",
                    namespace=namespace,
                    labels={"nodalarc.io/managed-by": "node-agent"},
                ),
                data=status_configmap_data(statuses, manifest),
            )
            v1.create_namespaced_config_map(namespace, body)
        else:
            raise
    ready_count = sum(1 for status in statuses.values() if status.status == "ready")
    failed_count = sum(1 for status in statuses.values() if status.status != "ready")
    log.info("Wrote wiring status: %d ready, %d failed", ready_count, failed_count)
