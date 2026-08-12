# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
from nodalarc.runtime_naming import is_managed_host_ifname
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
    enable_mpls_input,
)
from node_agent.pid_discovery import NamespaceHandle, discover_local_pod_handles

_IPTABLES_RULES = (
    "*filter\n"
    "-A OUTPUT -o cni0 -m state --state ESTABLISHED,RELATED -j ACCEPT\n"
    "-A OUTPUT -o cni0 -j DROP\n"
    "COMMIT\n"
)


def rename_cni_interface(pid: int, node_id: str) -> str | None:
    """Rename the CNI interface eth0 -> cni0, platform-owned and pre-workload.

    The rename must land before any workload starts so every routing engine
    learns the interface under its final name (zebra caches interface
    identity from startup). Idempotent: an already-renamed namespace is a
    no-op. Returns error string or None.
    """
    try:

        def _rename(ipr: IPRoute) -> None:
            eth = ipr.link_lookup(ifname="eth0")
            if not eth:
                if ipr.link_lookup(ifname="cni0"):
                    return
                raise RuntimeError("neither eth0 nor cni0 exists in the pod namespace")
            index = eth[0]
            ipr.link("set", index=index, state="down")
            ipr.link("set", index=index, ifname="cni0")
            ipr.link("set", index=index, state="up")

        _in_namespace(pid, _rename)
        return None
    except Exception as exc:
        return f"{node_id}: {exc}"


def remove_default_route(pid: int, node_id: str, cluster_pod_cidr: str | None = None) -> str | None:
    """Replace the pod's CNI default route with a scoped management route.

    The constellation is the data plane: the CNI default must not compete
    with the routing engine's default. But the management path (the browser
    terminal reaching this pod cross-node via VS-API, and any control-plane
    traffic to another node's pods) rides cni0, and a pod can only answer a
    peer it has a route to. So this drops the CNI default and installs a
    route to the cluster pod CIDR via the cni0 bridge gateway: the routing
    engine owns 0.0.0.0/0 while cni0 keeps a path to every pod in the
    cluster. A host-attachment default over terr0 is deliberate substrate
    state on a different interface and is untouched.
    """
    import ipaddress

    try:

        def _replace_default(ipr: IPRoute) -> None:
            cni_links = ipr.link_lookup(ifname="cni0")
            if not cni_links:
                return
            cni_index = cni_links[0]

            # The bridge gateway is deterministic and always present: it is
            # the first host address of the pod's own cni0 subnet (the CNI
            # bridge IP). Deriving it here — rather than capturing it from the
            # CNI default route — means the management route installs whether
            # that default is still present (fresh wire) or already gone (a
            # re-wire after the routing engine took the default). The default
            # is still deleted below when it exists on cni0.
            # The bridge gateway is the first host address of the pod's
            # connected cni0 subnet — the scope-link route whose prefix is a
            # real subnet (dst_len < 32), never a /32 host or broadcast route
            # (those also carry link scope and would derive a bogus gateway).
            gateway = None
            for route in ipr.get_routes(family=2):
                if route.get_attr("RTA_OIF") != cni_index:
                    continue
                dst = route.get_attr("RTA_DST")
                dst_len = route["dst_len"]
                if dst is None and dst_len == 0:
                    ipr.route(
                        "del",
                        dst="0.0.0.0/0",
                        gateway=route.get_attr("RTA_GATEWAY"),
                        oif=cni_index,
                    )
                elif dst is not None and dst_len < 32 and route["scope"] == 253:
                    subnet = ipaddress.ip_network(f"{dst}/{dst_len}", strict=False)
                    gateway = str(subnet.network_address + 1)

            if cluster_pod_cidr and gateway:
                # Idempotent: 'replace' tolerates a route left by a prior pass.
                ipr.route(
                    "replace",
                    dst=cluster_pod_cidr,
                    gateway=gateway,
                    oif=cni_index,
                )

        _in_namespace(pid, _replace_default)
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


def finalize_pod_network(
    pid: int, node_id: str, cluster_pod_cidr: str | None = None
) -> tuple[str | None, str | None]:
    """Rename the CNI interface, scope the default route, lock down cni0."""
    rename_err = rename_cni_interface(pid, node_id)
    route_err = remove_default_route(pid, node_id, cluster_pod_cidr)
    lockdown_err = lock_down_cni0(pid, node_id)
    security_errors = [err for err in (rename_err, lockdown_err) if err]
    return route_err, "; ".join(security_errors) if security_errors else None


def finalize_pod(pid: int, node_id: str) -> str | None:
    """Remove default route and lock down cni0. Returns combined error or None."""
    errors = [err for err in finalize_pod_network(pid, node_id) if err]
    return "; ".join(errors) if errors else None


log = logging.getLogger(__name__)


def _cleanup_stale_interfaces(
    pid_map: dict[str, int],
    nodes: dict,
    progress_fn: Callable[[str], None] | None = None,
) -> None:
    """Clean stale interfaces from host and pod namespaces.

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
            if ifname and is_managed_host_ifname(ifname):
                try:
                    ipr.link("del", index=link["index"])
                    host_cleaned += 1
                except Exception:
                    pass
    finally:
        ipr.close()
    if host_cleaned:
        log.info("Cleaned %d stale host interfaces", host_cleaned)

    # Host firewall: drop the pinned site-LAN transit rules alongside the
    # interfaces they served; the terrestrial phase re-pins them when this
    # generation wires site LANs.
    from node_agent.site_lan import remove_site_lan_transit

    remove_site_lan_transit()

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
                # Site-LAN veth transit name (pod end before its rename to
                # terr0) — stranded only if wiring crashed mid-move.
                or ifname.startswith("sp")
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
            "Cleaned %d stale pod interfaces across %d pods",
            pod_cleaned,
            len(pid_map),
        )


def expected_local_nodes(manifest: WiringManifest) -> set[str]:
    """The manifest nodes placed on this host — expectation, never discovery."""
    import os

    local_node = os.environ.get("NODE_NAME", "")
    if not local_node:
        raise RuntimeError("NODE_NAME is not set — cannot derive the expected-local pod set")
    return {node_id for node_id, spec in manifest.nodes.items() if spec.host == local_node}


def discover_expected_handles(
    manifest: WiringManifest,
    namespace: str,
    expected_local: set[str],
    *,
    max_attempts: int = 30,
) -> dict[str, NamespaceHandle] | None:
    """Return one complete validated handle set for the expected-local pods.

    Retries discovery (fenced to the manifest's deployment run) until every
    expected local pod has a validated handle, or returns None. None means
    pending: the caller must leave existing kernel state untouched and
    retry — a transient Kubernetes or CRI failure must never lead to the
    destruction of a healthy host data plane.
    """
    import time

    handles: dict[str, NamespaceHandle] = {}
    for attempt in range(1, max_attempts + 1):
        try:
            handles = discover_local_pod_handles(
                namespace,
                session_run_id=manifest.session_run_id,
                owner_uid=manifest.owner_uid,
            )
        except Exception as exc:
            # A transient Kubernetes or CRI failure is a pending attempt,
            # never a divergence signal.
            log.warning("Handle discovery attempt %d failed: %s", attempt, exc)
            handles = {}
        missing = expected_local - set(handles.keys())
        if not missing:
            return {node_id: handles[node_id] for node_id in expected_local}
        if attempt % 5 == 1:
            log.info(
                "Handle discovery attempt %d: %d/%d expected local pods validated",
                attempt,
                len(expected_local) - len(missing),
                len(expected_local),
            )
        if attempt < max_attempts:
            time.sleep(2)
    missing = expected_local - set(handles.keys())
    log.error(
        "Discovery incomplete: %d/%d expected local pods have no validated handle: %s",
        len(missing),
        len(expected_local),
        ", ".join(sorted(missing)),
    )
    return None


def execute_wiring(
    manifest: dict[str, Any] | WiringManifest,
    namespace: str,
    handles: dict[str, NamespaceHandle],
    progress_fn: Callable[[str], None] | None = None,
) -> dict[str, NodeWiringStatus]:
    """Execute all data plane wiring operations from a topology manifest.

    Args:
        manifest: Parsed wiring manifest from ConfigMap.
        namespace: K8s namespace for status writes.
        handles: The complete validated handle set for this host's expected
            pods, from discover_expected_handles. Wiring never discovers
            independently: the caller resolves one handle set, decides
            whether destruction is warranted, and passes that exact set in.
        progress_fn: Optional callback for real-time progress via NATS.

    Returns:
        {node_id: NodeWiringStatus} covering exactly the handled pods.
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
    cluster_pod_cidr = manifest_model.cluster_pod_cidr

    if not handles:
        log.info("No handles to wire on this node")
        return {}

    import os

    local_node = os.environ.get("NODE_NAME", "")
    if not local_node:
        raise RuntimeError("NODE_NAME is not set — cannot identify this host for wiring")

    pid_map: dict[str, int] = {node_id: handle.pid for node_id, handle in handles.items()}

    statuses: dict[str, NodeWiringStatus] = {}
    node_failures: dict[str, tuple[str, str]] = {}
    total_nodes = len(handles)

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

    # Clean stale interfaces from host and pod namespaces.
    # Must run BEFORE the ThreadPoolExecutor starts creating interfaces.
    # Without this, 8 concurrent threads racing to create and clean
    # interfaces produce EEXIST race conditions.
    _write_progress(f"Cleaning stale interfaces for {total_nodes} nodes")
    _cleanup_stale_interfaces(pid_map, nodes, progress_fn=progress_fn)

    # Configure sysctls in each pod namespace (via os.setns).
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

    # Create ISL veth pairs (deduplicate A→B and B→A, parallelized).
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
    log.info("Created %d host-mediated ISL pairs", len(created_links))
    requires_mpls = any(bool(node_spec.get("mpls_enable")) for node_spec in nodes.values())
    if requires_mpls:
        load_mpls_kernel_modules()
        _write_progress(f"Created {len(created_links)} ISL pairs. Enabling MPLS...")
    else:
        _write_progress(f"Created {len(created_links)} ISL pairs. MPLS not requested.")

    # Enable MPLS input on ISL interfaces (parallelized).
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
    log.info("MPLS input enabled on %d ISL interfaces", len(mpls_tasks))
    if requires_mpls:
        _write_progress(
            f"MPLS enabled on {len(mpls_tasks)} interfaces. Creating ground infrastructure..."
        )
    else:
        _write_progress("Creating ground infrastructure...")

    # Create ground infrastructure (parallelized).
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
        "Created %d ground bridges and %d satellite ground veths",
        gs_created,
        sat_gnd_created,
    )
    _write_progress(
        f"Ground infrastructure ready: {gs_created} GS, {sat_gnd_created} satellites. Creating terrestrial interfaces..."
    )

    # Wire site LANs (terr0 as bridge ports, parallelized per site).
    # A site's LAN is one L2 segment: per-host bridge, member terr0 veths as
    # ports, VXLAN head-end replication between hosts that share the site.
    from nodalarc.platform_config import get_platform_config

    from node_agent.site_lan import plan_site_lan, wire_site_lan

    local_ip = os.environ.get("HOST_IP", "")
    base_mtu = get_platform_config().veth_interface_mtu_bytes
    site_lan_specs = {
        site_id: spec.model_dump() for site_id, spec in manifest_model.site_lans.items()
    }

    def _local_site_members(spec: dict) -> list[str]:
        return [
            member["node_id"] for member in spec["members"] if pid_map.get(member["node_id"], 0) > 0
        ]

    site_plans = []
    for site_id, spec in site_lan_specs.items():
        try:
            plan = plan_site_lan(
                site_id,
                spec,
                nodes=nodes,
                pid_map=pid_map,
                local_node=local_node,
                local_ip=local_ip,
                base_mtu=base_mtu,
            )
        except Exception as exc:
            for member_id in _local_site_members(spec):
                _record_failure(member_id, "terrestrial_interfaces", f"site LAN plan failed: {exc}")
            continue
        if plan is not None:
            site_plans.append(plan)

    if site_plans:
        # Bridged transit on the site LANs must not be subject to host
        # firewall policy (br_netfilter + e.g. Docker's FORWARD DROP).
        # Failure poisons every local member: a LAN the host may police is
        # not wired, it only looks wired.
        from node_agent.site_lan import ensure_site_lan_transit

        try:
            ensure_site_lan_transit()
        except Exception as exc:
            log.exception("Site LAN transit rule installation failed")
            for plan in site_plans:
                for port in plan.local_members:
                    _record_failure(
                        port.node_id,
                        "terrestrial_interfaces",
                        f"site LAN transit rules failed: {exc}",
                    )
            site_plans = []

    wired_sites = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        site_futures = {pool.submit(wire_site_lan, plan): plan for plan in site_plans}
        for fut in as_completed(site_futures):
            plan = site_futures[fut]
            try:
                fut.result()
                wired_sites += 1
            except Exception as exc:
                log.exception("Site LAN %s wiring failed", plan.site_id)
                for port in plan.local_members:
                    _record_failure(
                        port.node_id,
                        "terrestrial_interfaces",
                        f"site LAN {plan.site_id} wiring failed: {exc}",
                    )
    log.info("%d site LANs wired on this host", wired_sites)
    _write_progress(
        f"Terrestrial interfaces created. Finalizing {total_nodes} pods (routes + security)..."
    )

    # Per-pod finalization: default route removal + cni0 lockdown.
    finalized = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fin_futures = {}
        for node_id in nodes:
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            fin_futures[pool.submit(finalize_pod_network, pid, node_id, cluster_pod_cidr)] = node_id
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
    log.info("Finalized %d pods (default route + cni0 lockdown)", finalized)
    _write_progress(f"Finalized {finalized}/{total_nodes} pods. Wiring complete.")

    # Mark only nodes with all required wiring phases successful as ready.
    # Every status row carries the exact pod incarnation that was wired, so
    # release gates can bind to it.
    for node_id, handle in handles.items():
        if node_id in node_failures:
            phase, message = node_failures[node_id]
            statuses[node_id] = failed_status(
                node_id,
                manifest_model,
                pod_uid=handle.pod_uid,
                sandbox_id=handle.sandbox_id,
                netns_id=handle.netns_id,
                phase=phase,
                error_message=message,
                dirty_kernel=True,
            )
        else:
            statuses[node_id] = ready_status(
                node_id,
                manifest_model,
                pod_uid=handle.pod_uid,
                sandbox_id=handle.sandbox_id,
                netns_id=handle.netns_id,
            )

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
