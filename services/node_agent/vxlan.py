# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""VXLAN tunnel management for cross-node ISL and GS links.

Creates and destroys per-link VXLAN tunnels between pods on different K3s
nodes. Each cross-node link gets a dedicated VXLAN interface (point-to-point,
no shared bridge, no broadcast domain).

Architecture per cross-node link (e.g., sat-P00S00 on nodal ↔ sat-P01S00 on nodal03):

    Host namespace (nodal):
      vxlan-{vni}  ←── tc mirred redirect ──→  veth-{vni}-h
      (VXLAN UDP endpoint)                      (host-end of veth pair)
                                                     │
                                                veth-{vni}-p → moved into pod → renamed to isl0

    Pod namespace (sat-P00S00):
      isl0 (veth pod-end) — FRR sees this as a normal interface

VXLAN must live in the HOST namespace because that's where the physical NIC
and routing table are. The pod-side is a veth, same as LOCAL ISL wiring.
tc mirred redirect connects the VXLAN to the veth host-end — same proven
pattern as ground_bridge.py's satellite attachment.
"""

from __future__ import annotations

import logging
import os

from nodalarc.vxlan import compute_vni  # noqa: F401 — re-export for convenience

from node_agent.ground_bridge import _tc_mirred_redirect, _tc_mirred_remove
from node_agent.namespace_ops import _get_host_ns_fd, _in_namespace, _libc, _ns_lock

log = logging.getLogger(__name__)

# VXLAN overhead: 8 VXLAN + 8 UDP + 20 IP + 14 outer Ethernet = 50 bytes
VXLAN_OVERHEAD_BYTES = 50

# Default destination port for VXLAN (IANA standard)
VXLAN_DST_PORT = 4789

# Clone flag for setns
_CLONE_NEWNET = 0x40000000


def _host_ifnames(vni: int) -> tuple[str, str, str]:
    """Deterministic host-side interface names from VNI. Max 15 chars each."""
    tag = f"{vni % 99999:05d}"
    return f"vx{tag}", f"vh{tag}", f"vp{tag}"


def create_vxlan_link(
    pid: int,
    ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
    mtu: int | None = None,
) -> None:
    """Create a VXLAN-backed interface in a pod namespace.

    1. Enter host namespace
    2. Create VXLAN interface (UDP endpoint to remote node)
    3. Create veth pair (host-end + pod-end)
    4. Install bidirectional tc mirred redirect: VXLAN ↔ veth host-end
    5. Move veth pod-end into target pod namespace
    6. Rename to target interface name, set MTU, bring UP

    Args:
        pid: PID of the target pod.
        ifname: Target interface name inside the pod (e.g., "isl0", "term0", "gnd0").
        local_ip: This node's IP (VXLAN local endpoint).
        remote_ip: Peer node's IP (VXLAN remote endpoint).
        vni: VXLAN Network Identifier.
        mtu: Inner MTU. Default: platform MTU - VXLAN overhead.
    """
    from pyroute2 import IPRoute

    if mtu is None:
        from nodalarc.platform_config import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes - VXLAN_OVERHEAD_BYTES

    vxlan_if, veth_host, veth_pod = _host_ifnames(vni)

    # Idempotent: if the target interface already exists in the pod, skip.
    # This happens when the Scheduler retries after a timeout — the prior
    # attempt completed but the ACK was lost.
    def _check_exists(ns_ipr):
        return bool(ns_ipr.link_lookup(ifname=ifname))

    if _in_namespace(pid, _check_exists):
        log.info("VXLAN link %s already exists in ns(%d), skipping create", ifname, pid)
        return

    # Get the target pod's namespace fd (while we can still see /proc/{pid})
    pod_ns_fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)

    try:
        # --- All host namespace operations under the ns lock ---
        import ctypes

        with _ns_lock:
            # Enter host namespace
            host_fd = _get_host_ns_fd()
            ret = _libc.setns(host_fd, _CLONE_NEWNET)
            if ret != 0:
                errno = ctypes.get_errno()
                raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")

            try:
                ipr = IPRoute()
                try:
                    # Idempotent: clean up stale interfaces from prior attempt
                    # (Case C — partial kernel state → cleanup then wire fresh)
                    for stale_name in [veth_host, vxlan_if]:
                        stale = ipr.link_lookup(ifname=stale_name)
                        if stale:
                            log.info("Cleaning stale %s before VXLAN create", stale_name)
                            ipr.link("del", index=stale[0])

                    # 1. Create VXLAN interface
                    ipr.link(
                        "add",
                        ifname=vxlan_if,
                        kind="vxlan",
                        vxlan_id=vni,
                        vxlan_local=local_ip,
                        vxlan_group=remote_ip,
                        vxlan_port=VXLAN_DST_PORT,
                        vxlan_learning=False,
                    )

                    # 2. Create veth pair
                    ipr.link(
                        "add",
                        ifname=veth_host,
                        kind="veth",
                        peer={"ifname": veth_pod},
                    )

                    # 3. Set MTU on all interfaces
                    for name in [vxlan_if, veth_host, veth_pod]:
                        links = ipr.link_lookup(ifname=name)
                        if links:
                            ipr.link("set", index=links[0], mtu=mtu)

                    # 4. Bring VXLAN and veth host-end UP (required for tc mirred)
                    for name in [vxlan_if, veth_host]:
                        links = ipr.link_lookup(ifname=name)
                        if links:
                            ipr.link("set", index=links[0], state="up")

                    # 5. Move veth pod-end into target pod namespace via fd
                    links = ipr.link_lookup(ifname=veth_pod)
                    if not links:
                        raise RuntimeError(f"veth pod-end {veth_pod} not found")
                    ipr.link("set", index=links[0], net_ns_fd=pod_ns_fd)

                finally:
                    ipr.close()

                # 6. Install bidirectional tc mirred redirect (in host namespace)
                _tc_mirred_redirect(vxlan_if, veth_host)
                _tc_mirred_redirect(veth_host, vxlan_if)

            finally:
                # Return to Node Agent's CNI namespace (not strictly necessary
                # since _in_namespace will re-enter host via _HOST_NS_FD, but
                # defensive — don't leave the thread in host namespace)
                pass
    finally:
        os.close(pod_ns_fd)

    # 7. Inside pod namespace: rename veth pod-end and bring UP
    def _configure_in_pod(ns_ipr):
        links = ns_ipr.link_lookup(ifname=veth_pod)
        if links:
            idx = links[0]
            ns_ipr.link("set", index=idx, ifname=ifname)
            ns_ipr.link("set", index=idx, state="up")

    _in_namespace(pid, _configure_in_pod)

    log.info(
        "Created VXLAN link %s in ns(%d): VNI=%d %s→%s MTU=%d [%s↔%s↔%s]",
        ifname,
        pid,
        vni,
        local_ip,
        remote_ip,
        mtu,
        vxlan_if,
        veth_host,
        veth_pod,
    )


def destroy_vxlan_link(pid: int, ifname: str, vni: int) -> None:
    """Remove a VXLAN link — destroys host-side VXLAN + veth and pod-side interface.

    Enters host namespace to clean up VXLAN interface, veth host-end, and
    tc mirred rules. The pod-side veth is automatically destroyed when the
    host-side is deleted (kernel cleans up veth pairs).
    """
    vxlan_if, veth_host, _veth_pod = _host_ifnames(vni)

    import ctypes

    with _ns_lock:
        host_fd = _get_host_ns_fd()
        ret = _libc.setns(host_fd, _CLONE_NEWNET)
        if ret != 0:
            errno = ctypes.get_errno()
            log.warning("setns to host failed during VXLAN cleanup: %s", os.strerror(errno))
            return

        try:
            from pyroute2 import IPRoute

            ipr = IPRoute()
            try:
                # Remove tc mirred rules
                _tc_mirred_remove(vxlan_if)
                _tc_mirred_remove(veth_host)

                # Delete veth host-end (kernel auto-deletes pod-end)
                links = ipr.link_lookup(ifname=veth_host)
                if links:
                    ipr.link("del", index=links[0])

                # Delete VXLAN interface
                links = ipr.link_lookup(ifname=vxlan_if)
                if links:
                    ipr.link("del", index=links[0])
            finally:
                ipr.close()
        except Exception as exc:
            log.warning("VXLAN link cleanup failed (VNI=%d): %s", vni, exc)

    log.info("Destroyed VXLAN link VNI=%d [%s + %s]", vni, vxlan_if, veth_host)


# ---------------------------------------------------------------------------
# Cross-node ground link — VXLAN between existing host-side interfaces
# ---------------------------------------------------------------------------


def attach_cross_node_ground(
    local_host_ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
    sat_pid: int | None = None,
    sat_ifname: str = "",
) -> None:
    """Connect a local host-side interface to a remote node via VXLAN.

    Used for CROSS_NODE GROUND links. The local host-side interface
    already exists from wiring (satellite's _gnd_{sat} or GS's _gbr-{gs}).
    This function creates a VXLAN tunnel in the host namespace and
    tc mirred redirects between the VXLAN and the existing host-side interface.

    Same pattern as LOCAL attach_to_ground_bridge but with VXLAN replacing
    the direct host-side veth connection.

    If sat_pid is provided, also brings satellite's gnd0 UP inside the pod.
    """
    import ctypes

    from pyroute2 import IPRoute

    vxlan_if, _, _ = _host_ifnames(vni)

    with _ns_lock:
        host_fd = _get_host_ns_fd()
        ret = _libc.setns(host_fd, _CLONE_NEWNET)
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")

        try:
            ipr = IPRoute()
            try:
                # Idempotent: clean up stale VXLAN from prior attempt
                stale = ipr.link_lookup(ifname=vxlan_if)
                if stale:
                    log.info("Cleaning stale %s before ground VXLAN attach", vxlan_if)
                    _tc_mirred_remove(vxlan_if)
                    _tc_mirred_remove(local_host_ifname)
                    ipr.link("del", index=stale[0])

                # Create VXLAN interface
                ipr.link(
                    "add",
                    ifname=vxlan_if,
                    kind="vxlan",
                    vxlan_id=vni,
                    vxlan_local=local_ip,
                    vxlan_group=remote_ip,
                    vxlan_port=VXLAN_DST_PORT,
                    vxlan_learning=False,
                )

                # Bring VXLAN and local host interface UP
                for name in [vxlan_if, local_host_ifname]:
                    links = ipr.link_lookup(ifname=name)
                    if links:
                        ipr.link("set", index=links[0], state="up")
            finally:
                ipr.close()

            # Bidirectional tc mirred redirect: VXLAN ↔ host-side interface
            _tc_mirred_redirect(vxlan_if, local_host_ifname)
            _tc_mirred_redirect(local_host_ifname, vxlan_if)
        finally:
            pass

    if sat_pid:
        if not sat_ifname:
            raise ValueError("sat_ifname required when sat_pid is provided")
        _target = sat_ifname

        def _up_sat_iface(ns_ipr):
            idx = ns_ipr.link_lookup(ifname=_target)
            if idx:
                ns_ipr.link("set", index=idx[0], state="up")

        _in_namespace(sat_pid, _up_sat_iface)

    log.info(
        "Attached cross-node ground: %s ↔ VXLAN VNI=%d (%s→%s)",
        local_host_ifname,
        vni,
        local_ip,
        remote_ip,
    )


def detach_cross_node_ground(
    local_host_ifname: str,
    vni: int,
    sat_pid: int | None = None,
    sat_ifname: str = "",
) -> None:
    """Disconnect a cross-node ground link.

    Removes tc mirred redirect, destroys VXLAN, brings host-side interface DOWN.
    If sat_pid provided, brings satellite pod-side interface DOWN.
    """
    import ctypes

    vxlan_if, _, _ = _host_ifnames(vni)

    if sat_pid:
        if not sat_ifname:
            raise ValueError("sat_ifname required when sat_pid is provided")
        _target = sat_ifname

        def _down_sat_iface(ns_ipr):
            idx = ns_ipr.link_lookup(ifname=_target)
            if idx:
                ns_ipr.link("set", index=idx[0], state="down")

        try:
            _in_namespace(sat_pid, _down_sat_iface)
        except Exception as exc:
            log.warning("Failed to down %s in ns(%d): %s", sat_ifname, sat_pid, exc)

    with _ns_lock:
        host_fd = _get_host_ns_fd()
        ret = _libc.setns(host_fd, _CLONE_NEWNET)
        if ret != 0:
            errno = ctypes.get_errno()
            log.warning("setns to host failed: %s", os.strerror(errno))
            return

        try:
            # Remove tc mirred
            _tc_mirred_remove(vxlan_if)
            _tc_mirred_remove(local_host_ifname)

            from pyroute2 import IPRoute

            ipr = IPRoute()
            try:
                # Bring host-side interface DOWN (carrier drops on pod gnd0)
                links = ipr.link_lookup(ifname=local_host_ifname)
                if links:
                    ipr.link("set", index=links[0], state="down")

                # Delete VXLAN
                links = ipr.link_lookup(ifname=vxlan_if)
                if links:
                    ipr.link("del", index=links[0])
            finally:
                ipr.close()
        except Exception as exc:
            log.warning("Cross-node ground detach failed (VNI=%d): %s", vni, exc)

    log.info("Detached cross-node ground: %s, VNI=%d", local_host_ifname, vni)
