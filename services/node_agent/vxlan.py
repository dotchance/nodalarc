"""VXLAN tunnel management for cross-node ISL links.

Creates and destroys per-link VXLAN interfaces in pod namespaces.
Each cross-node link gets a dedicated VXLAN tunnel (point-to-point,
no shared bridge, no broadcast domain).

All operations use pyroute2 — never shell commands (CLAUDE.md).
"""

from __future__ import annotations

import logging

from nodalarc.vxlan import compute_vni  # noqa: F401 — re-export for convenience

from node_agent.namespace_ops import _in_namespace

log = logging.getLogger(__name__)

# VXLAN overhead: 8 VXLAN + 8 UDP + 20 IP + 14 outer Ethernet = 50 bytes
VXLAN_OVERHEAD_BYTES = 50

# Default destination port for VXLAN (IANA standard)
VXLAN_DST_PORT = 4789


def create_vxlan_interface(
    pid: int,
    ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
    mtu: int | None = None,
) -> None:
    """Create a VXLAN interface and move it into a pod namespace.

    1. Create VXLAN interface on the host
    2. Move it into the target pod's network namespace
    3. Rename to target interface name (if needed)
    4. Set MTU, bring UP

    Args:
        pid: PID of the target pod (for namespace entry).
        ifname: Target interface name inside the pod (e.g., "isl0").
        local_ip: This node's IP (VXLAN local endpoint).
        remote_ip: Peer node's IP (VXLAN remote endpoint).
        vni: VXLAN Network Identifier (deterministic from link pair).
        mtu: Inner MTU. Default: platform MTU - VXLAN overhead.
    """
    from pyroute2 import IPRoute

    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes - VXLAN_OVERHEAD_BYTES

    # Temporary host-side name (must be unique, ≤15 chars)
    host_ifname = f"vx{vni % 99999:05d}"

    ipr = IPRoute()
    try:
        # Create VXLAN interface on the host
        ipr.link(
            "add",
            ifname=host_ifname,
            kind="vxlan",
            vxlan_id=vni,
            vxlan_local=local_ip,
            vxlan_group=remote_ip,
            vxlan_port=VXLAN_DST_PORT,
            vxlan_learning=False,
        )

        # Get the interface index
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise RuntimeError(f"VXLAN interface {host_ifname} not found after creation")
        idx = links[0]

        # Set MTU on host side before moving
        ipr.link("set", index=idx, mtu=mtu)

        # Move into pod namespace
        ipr.link("set", index=idx, net_ns_pid=pid)
    finally:
        ipr.close()

    # Inside the pod namespace: rename and bring UP
    def _configure_in_ns():
        ns_ipr = IPRoute()
        try:
            links = ns_ipr.link_lookup(ifname=host_ifname)
            if links:
                idx = links[0]
                ns_ipr.link("set", index=idx, ifname=ifname)
                ns_ipr.link("set", index=idx, state="up")
        finally:
            ns_ipr.close()

    _in_namespace(pid, _configure_in_ns)

    log.info(
        "Created VXLAN interface %s in ns(%d): VNI=%d, local=%s, remote=%s, MTU=%d",
        ifname,
        pid,
        vni,
        local_ip,
        remote_ip,
        mtu,
    )


def destroy_vxlan_interface(pid: int, ifname: str) -> None:
    """Remove a VXLAN interface from a pod namespace.

    Enters the pod namespace and deletes the interface. The kernel
    automatically cleans up the VXLAN tunnel endpoint.
    """

    def _destroy_in_ns():
        from pyroute2 import IPRoute

        ipr = IPRoute()
        try:
            links = ipr.link_lookup(ifname=ifname)
            if links:
                ipr.link("del", index=links[0])
        finally:
            ipr.close()

    try:
        _in_namespace(pid, _destroy_in_ns)
        log.info("Destroyed VXLAN interface %s in ns(%d)", ifname, pid)
    except Exception as exc:
        log.warning("Failed to destroy VXLAN %s in ns(%d): %s", ifname, pid, exc)
