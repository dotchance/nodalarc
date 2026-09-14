# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VXLAN tunnel management for cross-node ISL and GS links.

Creates and destroys per-link VXLAN tunnels between pods on different K3s
nodes. Each cross-node link gets a dedicated VXLAN interface (point-to-point,
no shared bridge, no broadcast domain).

Architecture per cross-node link (e.g., sat-P00S00 on nodal ↔ sat-P01S00 on nodal03):

    Host namespace (nodal):
      vx<vni>  ←── tc mirred redirect ──→  vh<vni>
      (VXLAN UDP endpoint)                 (host-end of veth pair)
                                                │
                                           vp<vni> → moved into pod → renamed to isl0

    Pod namespace (sat-P00S00):
      isl0 (veth pod-end) — FRR sees this as a normal interface

VXLAN must live in the HOST namespace because that's where the physical NIC
and routing table are. The pod-side is a veth, same as LOCAL ISL wiring.
tc mirred redirect connects the VXLAN to the veth host-end — same proven
pattern as ground_bridge.py's satellite attachment.

Existing resources under a link's names are never taken over. A creation
finds one of three states: nothing present, so it creates; a complete link
proven to be the requested one, so it reuses it (a Scheduler retry after a
lost acknowledgement); anything partial, conflicting or unprovable, so it
refuses and mutates nothing. The inventory covers the devices and the
ingress side of every host interface the link's redirects occupy.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.runtime_naming import VxlanHostNames, vxlan_host_ifnames
from nodalarc.vxlan import VXLAN_DST_PORT, VXLAN_OVERHEAD_BYTES

from node_agent import kernel_verifier
from node_agent.ground_bridge import _tc_mirred_remove, install_redirect_pair
from node_agent.namespace_ops import _get_host_ns_fd, _in_namespace, _libc, _ns_lock

log = logging.getLogger(__name__)

# Clone flag for setns
_CLONE_NEWNET = 0x40000000


@dataclass(frozen=True, slots=True)
class _LinkInventory:
    """What exists under a link's names: host devices, occupied ingress sides, the pod end."""

    devices: frozenset[str]
    ingress: Mapping[str, str]  # host interface -> kind of the qdisc on its ingress parent
    pod_end: kernel_verifier.PodVethEnd | None
    pod_ifname: str | None

    @property
    def absent(self) -> bool:
        return not self.devices and not self.ingress and self.pod_end is None

    def carries_redirect_side(self, name: str) -> bool:
        return self.ingress.get(name) == "ingress"

    def describe(self) -> tuple[str, ...]:
        """Evidence for a refusal; decides nothing."""
        lines = tuple(sorted(self.devices))
        lines += tuple(f"{name} ingress ({kind})" for name, kind in sorted(self.ingress.items()))
        if self.pod_end is not None:
            lines += (f"{self.pod_ifname}@pod",)
        return lines


def _inventory(
    ipr,
    *,
    devices: tuple[str, ...],
    ingress_of: tuple[str, ...],
    pod_end: kernel_verifier.PodVethEnd | None = None,
    pod_ifname: str | None = None,
) -> _LinkInventory:
    found_devices = frozenset(name for name in devices if ipr.link_lookup(ifname=name))
    ingress: dict[str, str] = {}
    for name in ingress_of:
        found = ipr.link_lookup(ifname=name)
        if not found:
            continue
        kind = kernel_verifier.ingress_qdisc_kind(ipr, found[0])
        if kind is not None:
            ingress[name] = kind
    return _LinkInventory(
        devices=found_devices, ingress=ingress, pod_end=pod_end, pod_ifname=pod_ifname
    )


def _enter_host_namespace() -> None:
    import ctypes

    ret = _libc.setns(_get_host_ns_fd(), _CLONE_NEWNET)
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")


def create_vxlan_link(
    pid: int,
    ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
    mtu: int | None = None,
) -> None:
    """Create a VXLAN-backed interface in a pod namespace, or reuse the proven one.

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

    names: VxlanHostNames = vxlan_host_ifnames(vni)
    pod_end = kernel_verifier.pod_veth_end(pid, ifname)

    # Get the target pod's namespace fd (while we can still see /proc/{pid})
    pod_ns_fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)

    try:
        with _ns_lock:
            _enter_host_namespace()
            with IPRoute() as ipr:
                found = _inventory(
                    ipr,
                    devices=(names.tunnel, names.host_veth, names.pod_veth),
                    ingress_of=(names.tunnel, names.host_veth),
                    pod_end=pod_end,
                    pod_ifname=ifname,
                )
                complete = (
                    {names.tunnel, names.host_veth} <= found.devices
                    and names.pod_veth not in found.devices
                    and found.carries_redirect_side(names.tunnel)
                    and found.carries_redirect_side(names.host_veth)
                    and pod_end is not None
                )
                proofs: tuple[kernel_verifier.Proof, ...] = ()
                if complete and pod_end is not None:
                    proofs = (
                        kernel_verifier.prove_vxlan_device(
                            ipr, names.tunnel, vni=vni, local_ip=local_ip, remote_ip=remote_ip
                        ),
                        kernel_verifier.prove_veth_peer(
                            ipr, names.host_veth, peer_ns_fd=pod_ns_fd, peer_ifindex=pod_end.ifindex
                        ),
                        kernel_verifier.prove_mirred_redirect(ipr, names.tunnel, names.host_veth),
                        kernel_verifier.prove_mirred_redirect(ipr, names.host_veth, names.tunnel),
                        # Facts NodalArc set once at creation on its own host devices:
                        # the MTU on both, and admin UP. The pod interface's MTU is
                        # NodalArc's too; its admin state is the workload's and is
                        # neither proven nor touched here.
                        kernel_verifier.prove_link_mtu(ipr, names.tunnel, mtu=mtu),
                        kernel_verifier.prove_link_mtu(ipr, names.host_veth, mtu=mtu),
                        kernel_verifier.prove_link_admin_up(ipr, names.tunnel),
                        kernel_verifier.prove_link_admin_up(ipr, names.host_veth),
                    )
                    if pod_end.kind != "veth":
                        proofs += (kernel_verifier.Proof.fail(f"pod {ifname} is not veth"),)
                    if pod_end.mtu != mtu:
                        proofs += (
                            kernel_verifier.Proof.fail(
                                f"pod {ifname} MTU mismatch",
                                f"device={ifname}",
                                f"expected={mtu}",
                                f"observed={pod_end.mtu}",
                            ),
                        )
                    host_index = ipr.link_lookup(ifname=names.host_veth)[0]
                    if pod_end.peer_ifindex != host_index:
                        proofs += (
                            kernel_verifier.Proof.fail(
                                f"pod {ifname} peer index mismatch",
                                f"expected_ifindex={host_index}",
                                f"actual_ifindex={pod_end.peer_ifindex}",
                            ),
                        )
                if kernel_verifier.reuse_or_refuse(
                    subject=f"VNI {vni}",
                    absent=found.absent,
                    complete=complete,
                    proofs=proofs,
                    evidence=found.describe(),
                ):
                    log.info(
                        "VXLAN link %s in ns(%d) VNI=%d already complete and proven, reusing [%s]",
                        ifname,
                        pid,
                        vni,
                        " ".join(found.describe()),
                    )
                    return

                # 1. Create VXLAN interface
                ipr.link(
                    "add",
                    ifname=names.tunnel,
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
                    ifname=names.host_veth,
                    kind="veth",
                    peer={"ifname": names.pod_veth},
                )

                # 3. Set MTU on all interfaces
                for name in names:
                    links = ipr.link_lookup(ifname=name)
                    if links:
                        ipr.link("set", index=links[0], mtu=mtu)

                # 4. Bring VXLAN and veth host-end UP (required for tc mirred)
                for name in (names.tunnel, names.host_veth):
                    links = ipr.link_lookup(ifname=name)
                    if links:
                        ipr.link("set", index=links[0], state="up")

                # 5. Move veth pod-end into target pod namespace via fd
                links = ipr.link_lookup(ifname=names.pod_veth)
                if not links:
                    raise RuntimeError(f"veth pod-end {names.pod_veth} not found")
                ipr.link("set", index=links[0], net_ns_fd=pod_ns_fd)

            # 6. Install bidirectional tc mirred redirect (in host namespace)
            install_redirect_pair(names.tunnel, names.host_veth)
    finally:
        os.close(pod_ns_fd)

    # 7. Inside pod namespace: rename veth pod-end and bring UP
    def _configure_in_pod(ns_ipr):
        links = ns_ipr.link_lookup(ifname=names.pod_veth)
        if links:
            idx = links[0]
            ns_ipr.link("set", index=idx, ifname=ifname)
            ns_ipr.link("set", index=idx, state="up")

    _in_namespace(pid, _configure_in_pod)

    log.debug(
        "Created VXLAN link %s in ns(%d): VNI=%d %s→%s MTU=%d [%s↔%s↔%s]",
        ifname,
        pid,
        vni,
        local_ip,
        remote_ip,
        mtu,
        names.tunnel,
        names.host_veth,
        names.pod_veth,
    )


def destroy_vxlan_link(pid: int, ifname: str, vni: int) -> None:
    """Remove a VXLAN link — destroys host-side VXLAN + veth and pod-side interface.

    Enters host namespace to clean up VXLAN interface, veth host-end, and
    tc mirred rules. The pod-side veth is automatically destroyed when the
    host-side is deleted (kernel cleans up veth pairs).
    """
    names = vxlan_host_ifnames(vni)

    with _ns_lock:
        _enter_host_namespace()
        try:
            from pyroute2 import IPRoute

            ipr = IPRoute()
            try:
                # Remove tc mirred rules
                _tc_mirred_remove(names.tunnel)
                _tc_mirred_remove(names.host_veth)

                # Delete veth host-end (kernel auto-deletes pod-end)
                links = ipr.link_lookup(ifname=names.host_veth)
                if links:
                    ipr.link("del", index=links[0])

                # Delete VXLAN interface
                links = ipr.link_lookup(ifname=names.tunnel)
                if links:
                    ipr.link("del", index=links[0])
            finally:
                ipr.close()
        except Exception as exc:
            log.warning("VXLAN link cleanup failed (VNI=%d): %s", vni, exc)
            raise

    log.debug("Destroyed VXLAN link VNI=%d [%s + %s]", vni, names.tunnel, names.host_veth)


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
    """Connect a cross-node ground link, or reuse the proven one.

    The host-side veth of the local node already exists (GS bridge port or
    satellite host-side veth). A dedicated VXLAN carries it to the peer host
    and tc mirred redirects join the two, both ways. The inventory covers the
    tunnel and the ingress side of both interfaces, so an occupied local
    interface is refused before anything is created.
    """
    from pyroute2 import IPRoute

    names = vxlan_host_ifnames(vni)

    with _ns_lock:
        _enter_host_namespace()
        with IPRoute() as ipr:
            if not ipr.link_lookup(ifname=local_host_ifname):
                raise FileNotFoundError(f"cross-node ground: {local_host_ifname} not found")
            found = _inventory(
                ipr, devices=(names.tunnel,), ingress_of=(names.tunnel, local_host_ifname)
            )
            complete = (
                names.tunnel in found.devices
                and found.carries_redirect_side(names.tunnel)
                and found.carries_redirect_side(local_host_ifname)
            )
            proofs: tuple[kernel_verifier.Proof, ...] = ()
            if complete:
                proofs = (
                    kernel_verifier.prove_vxlan_device(
                        ipr, names.tunnel, vni=vni, local_ip=local_ip, remote_ip=remote_ip
                    ),
                    kernel_verifier.prove_mirred_redirect(ipr, names.tunnel, local_host_ifname),
                    kernel_verifier.prove_mirred_redirect(ipr, local_host_ifname, names.tunnel),
                    # Host-side admin UP on the tunnel and the local host interface,
                    # the state creation sets below; nothing is configured on reuse.
                    kernel_verifier.prove_link_admin_up(ipr, names.tunnel),
                    kernel_verifier.prove_link_admin_up(ipr, local_host_ifname),
                )
            reuse = kernel_verifier.reuse_or_refuse(
                subject=f"VNI {vni}",
                absent=found.absent,
                complete=complete,
                proofs=proofs,
                evidence=found.describe(),
            )
            if reuse:
                log.info(
                    "Cross-node ground VNI=%d already complete and proven, reusing [%s]",
                    vni,
                    " ".join(found.describe()),
                )
            else:
                # Create VXLAN interface
                ipr.link(
                    "add",
                    ifname=names.tunnel,
                    kind="vxlan",
                    vxlan_id=vni,
                    vxlan_local=local_ip,
                    vxlan_group=remote_ip,
                    vxlan_port=VXLAN_DST_PORT,
                    vxlan_learning=False,
                )

                # Bring VXLAN and local host interface UP
                for name in (names.tunnel, local_host_ifname):
                    links = ipr.link_lookup(ifname=name)
                    if links:
                        ipr.link("set", index=links[0], state="up")
        if not reuse:
            # Bidirectional tc mirred redirect: VXLAN ↔ host-side interface
            install_redirect_pair(names.tunnel, local_host_ifname)

    if sat_pid:
        if not sat_ifname:
            raise ValueError("sat_ifname required when sat_pid is provided")
        _target = sat_ifname

        def _up_sat_iface(ns_ipr):
            idx = ns_ipr.link_lookup(ifname=_target)
            if idx:
                ns_ipr.link("set", index=idx[0], state="up")

        _in_namespace(sat_pid, _up_sat_iface)

    log.debug(
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
    names = vxlan_host_ifnames(vni)

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
            raise

    with _ns_lock:
        _enter_host_namespace()
        try:
            # Remove tc mirred
            _tc_mirred_remove(names.tunnel)
            _tc_mirred_remove(local_host_ifname)

            from pyroute2 import IPRoute

            ipr = IPRoute()
            try:
                # Bring host-side interface DOWN (carrier drops on pod gnd0)
                links = ipr.link_lookup(ifname=local_host_ifname)
                if links:
                    ipr.link("set", index=links[0], state="down")

                # Delete VXLAN
                links = ipr.link_lookup(ifname=names.tunnel)
                if links:
                    ipr.link("del", index=links[0])
            finally:
                ipr.close()
        except Exception as exc:
            log.warning("Cross-node ground detach failed (VNI=%d): %s", vni, exc)
            raise

    log.debug("Detached cross-node ground: %s, VNI=%d", local_host_ifname, vni)
