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

import errno
import logging
import socket
from collections.abc import Callable
from typing import Any

import kubernetes.client
import kubernetes.config
from nodalarc.platform_config import get_platform_config
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.wiring_status import (
    WIRING_STATUS_ANNOTATION,
    NodeWiringStatus,
    encode_status,
    failed_status,
    wiring_row,
)
from nodalarc.vxlan import host_path_mtu_for
from pydantic import ValidationError
from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError

from node_agent import cni0_lockdown
from node_agent.ground_bridge import (
    create_ground_bridge,
    create_mediated_isl,
    create_satellite_ground_veth,
)
from node_agent.mpls import configure_mpls_input, ensure_mpls_kernel_support
from node_agent.namespace_ops import (
    _in_namespace,
    _write_sysctl_in_netns,
    configure_interface,
)
from node_agent.pid_discovery import NamespaceHandle, discover_local_pod_handles
from node_agent.proof_delivery import deliver_proof_file, kubelet_pods_dir
from node_agent.substrate_monitor import prove_host_path_mtu

# The management VRF every session pod's Kubernetes interface (cni0) belongs
# to, and its route table. The name leaves mgmt0 and any VRF name a user's
# router configuration chooses free.
MANAGEMENT_VRF = "nodalarc-mgmt"
MANAGEMENT_VRF_TABLE = 20033


def move_cni_interface_to_management_vrf(pid: int, node_id: str) -> str | None:
    """Rename the pod's Kubernetes interface to cni0 and move it, with its routes, into a VRF.

    The emulated world has no Kubernetes pod network, so no route toward it
    may sit in the table the emulation routes with. The interface is renamed
    eth0 -> cni0 before any workload starts (zebra caches interface identity
    from startup) and joins its own VRF (``nodalarc-mgmt``). The routes the
    CNI installed through its gateway (its default routes, IPv4 and IPv6,
    whichever the pod network has, whatever gateway the CNI uses) are read
    while the interface is still up, because taking it down for the rename
    drops them, and are put back in the VRF's table. The main table then
    holds only what the emulation installs or learns. Inbound management (the
    browser terminal through VS-API) still works: sockets in the default VRF
    accept connections arriving on the VRF (``tcp_l3mdev_accept``), and their
    replies leave through the VRF's table. Idempotent; a run that finds cni0
    in the VRF without its gateway routes fails, since they cannot be
    recovered. Returns an error string, or None.
    """

    def _gateway_routes(ipr: IPRoute, index: int, table: int) -> list:
        return [
            (family, route.get_attr("RTA_DST"), route["dst_len"], route.get_attr("RTA_GATEWAY"))
            for family in (socket.AF_INET, socket.AF_INET6)
            for route in ipr.get_routes(family=family, table=table)
            if route.get_attr("RTA_OIF") == index and route.get_attr("RTA_GATEWAY")
        ]

    def _move(ipr: IPRoute) -> None:
        eth = ipr.link_lookup(ifname="eth0")
        cni = ipr.link_lookup(ifname="cni0")
        if not eth and not cni:
            raise RuntimeError("neither eth0 nor cni0 exists in the pod namespace")
        index = (eth or cni)[0]
        vrf_links = ipr.link_lookup(ifname=MANAGEMENT_VRF)
        if not vrf_links:
            ipr.link("add", ifname=MANAGEMENT_VRF, kind="vrf", vrf_table=MANAGEMENT_VRF_TABLE)
            vrf_links = ipr.link_lookup(ifname=MANAGEMENT_VRF)
        vrf_index = vrf_links[0]
        ipr.link("set", index=vrf_index, state="up")

        enslaved = ipr.get_links(index)[0].get_attr("IFLA_MASTER") == vrf_index
        if enslaved:
            routes = _gateway_routes(ipr, index, MANAGEMENT_VRF_TABLE)
            if not routes:
                raise RuntimeError(
                    "cni0 is in the management VRF without its CNI gateway routes; "
                    "they cannot be recovered in this pod"
                )
        else:
            # Read before the interface goes down: down drops them.
            routes = _gateway_routes(ipr, index, 254)
            if not routes:
                raise RuntimeError("the pod network interface has no CNI gateway route")
            ipr.link("set", index=index, state="down")
            if eth:
                ipr.link("set", index=index, ifname="cni0")
            # Joining while down: the kernel cycles an interface that joins a
            # VRF while up, which would drop the routes put back below.
            ipr.link("set", index=index, master=vrf_index)
            ipr.link("set", index=index, state="up")
            for family, dst, dst_len, gateway in routes:
                default = "0.0.0.0/0" if family == socket.AF_INET else "::/0"
                ipr.route(
                    "replace",
                    family=family,
                    dst=f"{dst}/{dst_len}" if dst else default,
                    gateway=gateway,
                    oif=index,
                    table=MANAGEMENT_VRF_TABLE,
                )
        in_vrf = _gateway_routes(ipr, index, MANAGEMENT_VRF_TABLE)
        if set(in_vrf) != set(routes):
            raise RuntimeError(f"cni0 gateway routes did not read back in the VRF: {in_vrf}")
        for key in ("net.ipv4.tcp_l3mdev_accept", "net.ipv4.udp_l3mdev_accept"):
            err = _write_sysctl_in_netns(pid, key, "1", already_in_ns=True)
            if err:
                raise RuntimeError(f"{key}: {err}")
        leftover = [
            route.get_attr("RTA_DST") or "default"
            for family in (socket.AF_INET, socket.AF_INET6)
            for route in ipr.get_routes(family=family, table=254)
            if route.get_attr("RTA_OIF") == index
        ]
        if leftover:
            raise RuntimeError(f"cni0 routes still in the main table: {leftover}")

    try:
        _in_namespace(pid, _move)
        return None
    except Exception as exc:
        return f"{node_id}: {exc}"


def lock_down_cni0(pid: int, node_id: str) -> str | None:
    """Apply the cni0 lockdown in the pod (``cni0_lockdown``). Returns error string or None."""
    try:
        _in_namespace(pid, cni0_lockdown.lock_down_cni0_here)
        return None
    except Exception as exc:
        return f"{node_id}: {exc}"


def finalize_pod_network(pid: int, node_id: str) -> tuple[str | None, str | None]:
    """Rename the CNI interface, move it into the management VRF, lock down cni0."""
    move_err = move_cni_interface_to_management_vrf(pid, node_id)
    lockdown_err = lock_down_cni0(pid, node_id)
    return move_err, lockdown_err


log = logging.getLogger(__name__)


def _cleanup_stale_interfaces(
    pid_map: dict[str, int],
    nodes: dict,
    progress_fn: Callable[[str], None] | None = None,
) -> None:
    """Clean stale interfaces from pod namespaces, or fail naming what remains.

    The host's interfaces are cleaned by the caller before wiring starts
    (perform_rewire runs the one host cleaner on every rewire).

    Must run synchronously BEFORE the ThreadPoolExecutor starts.
    Prevents EEXIST race conditions when 32 threads create interfaces
    concurrently on a Node Agent that restarted with stale kernel state.
    """
    if progress_fn:
        progress_fn(f"Cleaning stale interfaces for {len(pid_map)} pods")

    # Pod namespaces: remove stale isl* and gnd0 interfaces
    failures: list[str] = []

    def _clean_stale_pod_ifaces(ns_ipr: IPRoute) -> tuple[int, list[str]]:
        cleaned = 0
        failed: list[str] = []
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
                try:
                    ns_ipr.link("del", index=link["index"])
                    cleaned += 1
                except NetlinkError as exc:
                    if exc.code != errno.ENODEV:  # already gone is absence
                        failed.append(f"{ifname}: {exc}")
        return cleaned, failed

    pod_cleaned = 0
    for node_id, pid in pid_map.items():
        if pid == 0:
            continue
        cleaned, failed = _in_namespace(pid, _clean_stale_pod_ifaces)
        pod_cleaned += cleaned
        failures.extend(f"{node_id}/{item}" for item in failed)
    if failures:
        raise RuntimeError("stale pod interfaces could not be removed: " + "; ".join(failures[:10]))
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
    superseded: Callable[[], bool],
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

    # The manifest owns each node's MPLS requirement; discovery binds it to
    # the handle before the handle can be published, so no consumer reads a
    # default. Rebuilt on every discovery: replacement and Case B included.
    requirements = {node_id: manifest.nodes[node_id].mpls_enable for node_id in expected_local}
    handles: dict[str, NamespaceHandle] = {}
    for attempt in range(1, max_attempts + 1):
        if superseded():
            # The manifest this discovery serves changed or was removed: its
            # pods are no longer the ones to wire.
            log.info("Handle discovery abandoned: the wiring manifest changed")
            return None
        try:
            handles = discover_local_pod_handles(
                namespace,
                session_run_id=manifest.session_run_id,
                owner_uid=manifest.owner_uid,
                requirements=requirements,
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


def _host_path_refusal(manifest: WiringManifest, local_node: str) -> str | None:
    """Prove each host path this host's session traffic can take, or say why not.

    The required size is the emulated MTU plus the VXLAN encapsulation for
    the target's address family.
    """
    from concurrent.futures import ThreadPoolExecutor

    inner_mtu = get_platform_config().veth_interface_mtu_bytes
    pairs = [pair for pair in manifest.required_substrate_pairs if pair.source_node == local_node]
    failures: list[str] = []
    # Each target's path is proven independently and at the same time.
    with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as pool:
        proofs = list(
            pool.map(
                lambda pair: prove_host_path_mtu(
                    pair, host_path_mtu_for(inner_mtu, pair.target_ip)
                ),
                pairs,
            )
        )
    for proof in proofs:
        if proof.carried:
            log.info("Host path proven: %s", proof.diagnostic())
        else:
            failures.append(proof.diagnostic())
    if not failures:
        return None
    return (
        f"the host network does not carry {inner_mtu}-byte emulated packets inside VXLAN: "
        + "; ".join(failures)
    )


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

    # The host network carries every emulated packet whole inside VXLAN, so
    # no link's MTU depends on where its pods run. Proven once per wiring
    # attempt, before anything is created, to every host this one shares a
    # session path with. A failure refuses this host's wiring with nothing
    # touched.
    host_path_refusal = _host_path_refusal(manifest_model, local_node)
    if host_path_refusal is not None:
        log.error("Host path refused: %s", host_path_refusal)
        return {
            node_id: failed_status(
                node_id,
                manifest_model,
                pod_uid=handle.pod_uid,
                sandbox_id=handle.sandbox_id,
                netns_id=handle.netns_id,
                phase="host_path_mtu",
                error_message=host_path_refusal,
            )
            for node_id, handle in handles.items()
        }

    def _record_failure(node_id: str, phase: str, message: str) -> None:
        node_failures.setdefault(node_id, (phase, message))
        log.warning("%s failed for %s: %s", phase, node_id, message)

    def _write_progress(phase_msg: str) -> None:
        """Publish wiring progress over NATS; the VS-API relays it to browsers."""
        if progress_fn is not None:
            progress_fn(phase_msg)

    # Clean stale interfaces from host and pod namespaces.
    # Must run BEFORE the ThreadPoolExecutor starts creating interfaces.
    # Without this, 8 concurrent threads racing to create and clean
    # interfaces produce EEXIST race conditions.
    _write_progress(f"Cleaning stale interfaces for {total_nodes} nodes")
    _cleanup_stale_interfaces(pid_map, nodes, progress_fn=progress_fn)

    # MPLS kernel support, established before its first use. Once per host per
    # wiring attempt that needs it; the kernel's current state decides, nothing
    # is cached. A refused node keeps this diagnostic as its failure and gets
    # no MPLS sysctl write anywhere below (the sysctl loop, the ISL enable, the
    # ground-interface enable); its other wiring is unchanged.
    requires_mpls = any(bool(node_spec.get("mpls_enable")) for node_spec in nodes.values())
    mpls_refused: set[str] = set()
    if requires_mpls:
        support = ensure_mpls_kernel_support()
        if support.available:
            log.info("MPLS kernel support verified: %s", support.diagnostic())
        else:
            message = f"MPLS kernel support unavailable: {support.diagnostic()}"
            for node_id, node_spec in nodes.items():
                if node_spec.get("mpls_enable") and pid_map.get(node_id, 0):
                    _record_failure(node_id, "mpls", message)
                    mpls_refused.add(node_id)

    # Configure sysctls in each pod namespace (via os.setns).
    sysctl_ok = 0
    sysctl_skipped = []
    for node_id, node_spec in nodes.items():
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            sysctl_skipped.append(node_id)
            continue
        for key, value in node_spec.get("sysctls", {}).items():
            if node_id in mpls_refused and key.startswith("net.mpls."):
                continue
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
    # Every ISL endpoint this attempt created or reused, with the creator's
    # decision, for the MPLS step below. Entries whose peer lives on another
    # host are not here: their interfaces are created at LinkUp.
    isl_endpoints: list[tuple[str, int, str, bool]] = []
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
            futures[fut] = (nid_a, nid_b, pid_a, pid_b, ifname_a, ifname_b)
        total_isls = len(futures)
        for fut in as_completed(futures):
            nid_a, nid_b, pid_a, pid_b, ifname_a, ifname_b = futures[fut]
            try:
                isl = fut.result()
                isl_endpoints.append((nid_a, pid_a, ifname_a, isl.created_a))
                isl_endpoints.append((nid_b, pid_b, ifname_b, isl.created_b))
                created_links.add((min(nid_a, nid_b), max(nid_a, nid_b)))
                if len(created_links) % 25 == 0 or len(created_links) == total_isls:
                    _write_progress(
                        f"Creating ISL interfaces: {len(created_links)}/{total_isls} pairs"
                    )
            except Exception as exc:
                _record_failure(nid_a, "isl_interfaces", f"mediated ISL to {nid_b}: {exc}")
                _record_failure(nid_b, "isl_interfaces", f"mediated ISL to {nid_a}: {exc}")
    log.info("Created %d host-mediated ISL pairs", len(created_links))
    if requires_mpls:
        _write_progress(f"Created {len(created_links)} ISL pairs. Enabling MPLS...")
    else:
        _write_progress(f"Created {len(created_links)} ISL pairs. MPLS not requested.")

    # MPLS input on the ISL endpoints this attempt created or reused, for the
    # nodes that require it (parallelized); a created endpoint is written and
    # read back, a reused one is read and a mismatch refused; never on a
    # refused node, never on an interface that does not exist yet.
    mpls_endpoints = [
        (nid, pid, ifname, created)
        for nid, pid, ifname, created in isl_endpoints
        if nodes[nid].get("mpls_enable") and nid not in mpls_refused
    ]
    mpls_verified = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(
                configure_mpls_input, pid, ifname, created=created, subject=f"ISL {nid}/{ifname}"
            ): (nid, ifname)
            for nid, pid, ifname, created in mpls_endpoints
        }
        for fut in as_completed(futures):
            nid, ifname = futures[fut]
            try:
                fut.result()
                mpls_verified += 1
            except Exception as exc:
                _record_failure(nid, "mpls", str(exc))
    log.info(
        "MPLS input configured and read back on %d of %d ISL endpoints",
        mpls_verified,
        len(mpls_endpoints),
    )
    if requires_mpls:
        _write_progress(
            f"MPLS input verified on {mpls_verified} ISL endpoints. Creating ground infrastructure..."
        )
    else:
        _write_progress("Creating ground infrastructure...")

    # Create ground infrastructure (parallelized).
    # Ground bridges (GS-side) and satellite ground veths are independent
    # and can be created concurrently. gnd0 starts admin DOWN; FRR zebra
    # brings it admin UP (no `shutdown` in config). With no host-side veth
    # connected, gnd0 enters LOWERLAYERDOWN (admin UP, no carrier).

    class _MplsStepFailed(Exception):
        """The ground interface exists; its MPLS input step failed. Recorded under ``mpls``."""

    def _ground_mpls(pid: int, ifname: str, node_id: str, created: bool) -> None:
        try:
            configure_mpls_input(pid, ifname, created=created, subject=f"ground {node_id}/{ifname}")
        except Exception as exc:
            raise _MplsStepFailed(str(exc)) from exc

    def _create_ground_bridge_task(gs_id: str, gs_pid: int, gnd_ifaces: list, mpls: bool) -> None:
        for iface_spec in gnd_ifaces:
            ifname = iface_spec["name"]
            veth = create_ground_bridge(gs_id, gs_pid, ifname=ifname)
            configure_interface(gs_pid, ifname, gs_id)
            if mpls:
                _ground_mpls(gs_pid, ifname, gs_id, veth.created)

    def _create_sat_ground_task(node_id: str, pid: int, gnd_ifaces: list, mpls: bool) -> None:
        for iface_spec in gnd_ifaces:
            ifname = iface_spec["name"]
            veth = create_satellite_ground_veth(node_id, pid, ifname=ifname)
            configure_interface(pid, ifname, node_id)
            if mpls:
                _ground_mpls(pid, ifname, node_id, veth.created)

    with ThreadPoolExecutor(max_workers=8) as pool:
        gnd_futures = {}
        for gs_id, _bridge_spec in ground_bridges.items():
            gs_pid = pid_map.get(gs_id, 0)
            if gs_pid == 0:
                log.warning("No PID for ground station %s", gs_id)
                continue
            gs_node = nodes.get(gs_id, {})
            gs_ifaces = gs_node["gnd_interfaces"]
            gs_mpls = bool(gs_node.get("mpls_enable", False)) and gs_id not in mpls_refused
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
            sat_mpls = bool(node_spec.get("mpls_enable", False)) and node_id not in mpls_refused
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
            except _MplsStepFailed as exc:
                _record_failure(nid, "mpls", str(exc))
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
        # Emulated LAN bridges live in the emulated_lan namespace, outside the
        # server's firewall (node_agent.emulated_lan). Failure poisons every
        # local member: a site LAN without its namespace is not wired.
        from node_agent.emulated_lan import ensure_emulated_lan_namespace

        try:
            ensure_emulated_lan_namespace()
        except Exception as exc:
            log.exception("Emulated LAN namespace could not be created")
            for plan in site_plans:
                for port in plan.local_members:
                    _record_failure(
                        port.node_id,
                        "terrestrial_interfaces",
                        f"emulated LAN namespace failed: {exc}",
                    )
            site_plans = []

    wired_sites = 0
    site_mpls_members = 0
    site_mpls_verified = 0
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
                continue
            # The site LAN creator removes stale devices and creates every
            # member veth afresh, so the pod-side interface it just gave each
            # member is created, never reused: a member whose node requires
            # MPLS gets the creating operation's input step, written and read
            # back, with a failure recorded under the mpls phase for that
            # member alone. Members without the requirement, and members of a
            # node the capability check refused, are not touched.
            for port in plan.local_members:
                node_spec = nodes.get(port.node_id) or {}
                if not node_spec.get("mpls_enable") or port.node_id in mpls_refused:
                    continue
                site_mpls_members += 1
                try:
                    configure_mpls_input(
                        port.pid,
                        port.interface,
                        created=True,
                        subject=f"site LAN {plan.site_id}/{port.node_id}/{port.interface}",
                    )
                    site_mpls_verified += 1
                except Exception as exc:
                    _record_failure(port.node_id, "mpls", str(exc))
    log.info("%d site LANs wired on this host", wired_sites)
    if site_mpls_members:
        log.info(
            "MPLS input configured and read back on %d of %d site LAN member interfaces",
            site_mpls_verified,
            site_mpls_members,
        )
    _write_progress(
        f"Terrestrial interfaces created. Finalizing {total_nodes} pods (routes + security)..."
    )

    # Per-pod finalization: cni0 into the management VRF, then the cni0 lockdown.
    finalized = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fin_futures = {}
        for node_id in nodes:
            pid = pid_map.get(node_id, 0)
            if pid == 0:
                continue
            fin_futures[pool.submit(finalize_pod_network, pid, node_id)] = node_id
        total_to_finalize = len(fin_futures)
        for fut in as_completed(fin_futures):
            nid = fin_futures[fut]
            try:
                move_err, security_err = fut.result()
                if move_err:
                    _record_failure(nid, "pod_route_finalization", move_err)
                if security_err:
                    _record_failure(nid, "pod_security", security_err)
                if not move_err and not security_err:
                    finalized += 1
                if finalized % 10 == 0 or finalized == total_to_finalize:
                    _write_progress(
                        f"Finalizing pods: {finalized}/{total_to_finalize} (management VRF)"
                    )
            except Exception as exc:
                _record_failure(nid, "pod_security", str(exc))
    log.info("Finalized %d pods (cni0 management VRF + lockdown)", finalized)
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
            statuses[node_id] = wiring_row(
                node_id,
                manifest_model,
                pod_uid=handle.pod_uid,
                sandbox_id=handle.sandbox_id,
                netns_id=handle.netns_id,
                state="ready",
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


# Concurrent pod PATCHes per status write; the host's pods are written together.
_STATUS_WRITE_WORKERS = 16


def write_wiring_status(
    statuses: dict[str, NodeWiringStatus],
    handles: dict[str, NamespaceHandle],
    namespace: str,
) -> None:
    """Write each node's wiring proof onto the pod it proves, then deliver it to the pod.

    The proof lives on the pod as an annotation, which the Operator and the
    Scheduler read. Every PATCH carries the pod UID the proof names, so a
    proof can never land on a replaced pod of the same name: the API server
    refuses a PATCH whose UID differs. After its PATCH, the same proof is
    written into the pod's wiring-status volume, where the pod's release gate
    reads it. All writes must succeed; any failure raises with every failed
    pod named.
    """
    from concurrent.futures import ThreadPoolExecutor

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods_dir = kubelet_pods_dir()

    def _write(node_id: str) -> str | None:
        status = statuses[node_id]
        handle = handles.get(node_id)
        if handle is None:
            return f"{node_id}: no namespace handle names its pod"
        if handle.pod_uid != status.pod_uid:
            return f"{node_id}: proof names pod {status.pod_uid}, handle names {handle.pod_uid}"
        encoded = encode_status(status)
        body = {
            "metadata": {
                "uid": status.pod_uid,
                "annotations": {WIRING_STATUS_ANNOTATION: encoded},
            }
        }
        try:
            v1.patch_namespaced_pod(handle.pod_name, namespace, body)
        except kubernetes.client.rest.ApiException as exc:
            return f"{node_id}: pod {handle.pod_name} uid={status.pod_uid}: HTTP {exc.status} {exc.reason}"
        try:
            deliver_proof_file(pods_dir, status.pod_uid, encoded)
        except OSError as exc:
            return f"{node_id}: pod {handle.pod_name} uid={status.pod_uid}: proof file: {exc}"
        return None

    with ThreadPoolExecutor(max_workers=_STATUS_WRITE_WORKERS) as pool:
        failures = [failure for failure in pool.map(_write, sorted(statuses)) if failure]
    if failures:
        raise RuntimeError(
            f"wiring proof write failed for {len(failures)} pod(s): " + "; ".join(failures[:10])
        )
    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status.status] = counts.get(status.status, 0) + 1
    log.info(
        "Wrote wiring proof to %d pods: %s",
        len(statuses),
        ", ".join(f"{count} {state}" for state, count in sorted(counts.items())),
    )
