# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Host-side link infrastructure: wiring and runtime operations.

All host-mediated veth creation (fixed interfaces and terminal interfaces assigned per pass)
and tc mirred redirect management. Used by both wiring.py (deploy-time) and handlers.py
(runtime).

tc mirred operations use pyroute2 native netlink tc calls (no subprocess).
Namespace operations use _in_namespace() from namespace_ops.py (setns, no fork).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from nodalarc.runtime_naming import (
    MANAGED_HOST_DEVICE_GROUP,
    isl_host_name,
    terminal_host_name,
)
from pyroute2 import IPRoute
from pyroute2.netlink.rtnl import TC_H_INGRESS

from node_agent import kernel_verifier
from node_agent.namespace_ops import _in_namespace, set_interface_admin, switch_off_host_ipv6

log = logging.getLogger(__name__)

# One lock per host-side terminal port. An attach or a detach holds the locks of both ports it
# joins, so a detach cannot delete the ingress qdisc an attach on either port just created.
# Rejected alternative: one lock per ground station, which left a space node's port unguarded
# when its two passes ran to two stations, and had no meaning on a link with no station.
_port_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

# A pod interface whose admin state the Node Agent sets with its link: (pid, interface name).
type PodEnd = tuple[int, str]


@contextlib.contextmanager
def _ports_locked(*host_ifnames: str) -> Iterator[None]:
    """Hold the lock of each named host port. The locks are taken in name order, so two
    operations that share a port never wait on each other in a cycle."""
    with contextlib.ExitStack() as stack:
        for name in sorted(set(host_ifnames)):
            stack.enter_context(_port_locks[name])
        yield


# ---------------------------------------------------------------------------
# TC mirred redirect (link_manager.py L734-779)
# ---------------------------------------------------------------------------


def install_redirect_pair(a: str, b: str) -> None:
    """Join two host-side interfaces with mirred redirects both ways, or reuse the proven pair.

    The one installation policy for redirects. Neither interface carries an
    ingress qdisc: install both. Both carry exactly the redirect toward the
    other: reuse. Anything else, whatever occupies the ingress side, is
    refused as `KernelStateConflict` and nothing is deleted or replaced.
    """
    with IPRoute() as ipr:
        indexes = {}
        for name in (a, b):
            found = ipr.link_lookup(ifname=name)
            if not found:
                raise FileNotFoundError(f"tc mirred: interface {name} not found")
            indexes[name] = found[0]
        occupied: dict[str, str] = {}
        for name in (a, b):
            kind = kernel_verifier.ingress_qdisc_kind(indexes[name])
            if kind is not None:
                occupied[name] = kind
        proofs: tuple[kernel_verifier.Proof, ...] = ()
        if len(occupied) == 2:
            proofs = (
                kernel_verifier.prove_mirred_redirect(ipr, a, b),
                kernel_verifier.prove_mirred_redirect(ipr, b, a),
            )
        if kernel_verifier.reuse_or_refuse(
            subject=f"redirect {a}<->{b}",
            absent=not occupied,
            complete=len(occupied) == 2,
            proofs=proofs,
            evidence=tuple(f"{name} ingress ({kind})" for name, kind in occupied.items()),
        ):
            log.info("Redirect pair %s<->%s already installed and proven, reusing", a, b)
            return
    _tc_mirred_redirect(a, b)
    _tc_mirred_redirect(b, a)


def _tc_mirred_redirect(src: str, dst: str) -> None:
    """Install tc ingress + mirred egress redirect from src to dst.

    Create-only: `install_redirect_pair` has established that ``src`` carries
    no ingress qdisc. Uses pyroute2 native netlink tc calls. No subprocess,
    no fork. Operates in the host network namespace (no setns needed).
    """
    ipr = IPRoute()
    try:
        src_idx = ipr.link_lookup(ifname=src)
        if not src_idx:
            raise FileNotFoundError(f"tc mirred: source interface {src} not found")
        dst_idx = ipr.link_lookup(ifname=dst)
        if not dst_idx:
            raise FileNotFoundError(f"tc mirred: destination interface {dst} not found")

        # Add ingress qdisc
        ipr.tc("add", index=src_idx[0], kind="ingress")

        # Add u32 match-all filter with mirred egress redirect action
        try:
            ipr.tc(
                "add-filter",
                kind="u32",
                index=src_idx[0],
                parent=TC_H_INGRESS,
                protocol=3,  # ETH_P_ALL
                target=0x00010000,
                keys=["0x0/0x0+0"],
                action={
                    "kind": "mirred",
                    "direction": "egress",
                    "action": "redirect",
                    "ifindex": dst_idx[0],
                },
            )
        except Exception as exc:
            log.error("tc mirred filter %s->%s failed: %s", src, dst, exc)
            raise
    finally:
        ipr.close()


def _tc_mirred_remove(ifname: str) -> None:
    """Remove tc ingress qdisc (and all its filters) from an interface.

    Uses pyroute2 native netlink. Idempotent — silently ignores missing qdisc.
    """
    ipr = IPRoute()
    try:
        links = ipr.link_lookup(ifname=ifname)
        if links:
            with contextlib.suppress(Exception):
                ipr.tc("del", index=links[0], kind="ingress")
    finally:
        ipr.close()


def attach_terminal_end(own_host: str, peer_host: str, pod_end: PodEnd | None) -> None:
    """Join one end of a long-delay pass on one host: both host ports up, this end's redirect
    toward its peer (``own_host`` ingress to ``peer_host`` egress), and the end's pod interface
    up when its admin state follows the link (``pod_end``). Each end of a long-delay pass
    installs and removes its own direction (``remove_redirect``), so a pass that reopens while
    its other end still drains finds that end's direction in place. A redirect already proven
    toward the peer is reused; any other occupant of the ingress side is refused and nothing is
    replaced."""
    with _ports_locked(own_host, peer_host):
        with IPRoute() as ipr:
            for name in (own_host, peer_host):
                idx = ipr.link_lookup(ifname=name)
                if not idx:
                    raise FileNotFoundError(f"{name} not found")
                ipr.link("set", index=idx[0], state="up")
            kind = kernel_verifier.ingress_qdisc_kind(ipr.link_lookup(ifname=own_host)[0])
            proofs: tuple[kernel_verifier.Proof, ...] = ()
            if kind is not None:
                proofs = (kernel_verifier.prove_mirred_redirect(ipr, own_host, peer_host),)
            reuse = kernel_verifier.reuse_or_refuse(
                subject=f"redirect {own_host}->{peer_host}",
                absent=kind is None,
                complete=kind is not None,
                proofs=proofs,
                evidence=(f"{own_host} ingress ({kind})",) if kind is not None else (),
            )
        if not reuse:
            _tc_mirred_redirect(own_host, peer_host)
        if pod_end is not None:
            set_interface_admin(*pod_end, up=True)


def remove_redirect(own_host: str, peer_host: str) -> None:
    """Remove one host port's outgoing redirect: the end of a long-delay pass that closed while
    its peer still drains. The peer's redirect toward it stays until the peer closes.
    Serialized with every attach and detach on either port."""
    with _ports_locked(own_host, peer_host):
        _tc_mirred_remove(own_host)


# ---------------------------------------------------------------------------
# Attach / detach of one pass between two terminal interfaces on this host
# ---------------------------------------------------------------------------


def attach_terminals(host_a: str, host_b: str, pod_ends: Sequence[PodEnd]) -> None:
    """Join two terminal interfaces for one pass.

    Brings both host ports admin UP, which gives carrier to both pod interfaces; brings up each
    pod interface whose admin state follows the link (``pod_ends``); then installs the redirect
    between the two host ports, both ways. Either port serves another peer on its next pass,
    so the redirect is installed at each attach and removed at each detach.
    """
    with _ports_locked(host_a, host_b):
        with IPRoute() as ipr:
            for name in (host_a, host_b):
                idx = ipr.link_lookup(ifname=name)
                if not idx:
                    raise FileNotFoundError(f"{name} not found")
                ipr.link("set", index=idx[0], state="up")
        for pod_end in pod_ends:
            set_interface_admin(*pod_end, up=True)
        install_redirect_pair(host_a, host_b)
    log.debug("Attached %s <-> %s (tc redirect)", host_a, host_b)


def detach_terminals(host_a: str, host_b: str, pod_ends: Sequence[PodEnd]) -> None:
    """End one pass between two terminal interfaces.

    Removes the redirect between the two host ports, brings down each pod interface whose
    admin state follows the link (``pod_ends``), then brings both host ports admin DOWN. A host
    port DOWN drops carrier on its pod interface (UP to LOWERLAYERDOWN), so a routing stack
    behind either end tears its adjacency down at once.
    """
    with _ports_locked(host_a, host_b):
        _tc_mirred_remove(host_a)
        _tc_mirred_remove(host_b)
        for pod_end in pod_ends:
            set_interface_admin(*pod_end, up=False, must_exist=False)
        with IPRoute() as ipr:
            for name in (host_a, host_b):
                idx = ipr.link_lookup(ifname=name)
                if idx:
                    ipr.link("set", index=idx[0], state="down")
    log.debug("Detached %s from %s", host_a, host_b)


# ---------------------------------------------------------------------------
# ISL host-mediated attach / detach
# ---------------------------------------------------------------------------


def _isl_host_name(node_id: str, isl_idx: int) -> str:
    """Host-side veth name for ISL endpoint. ≤15 chars."""
    return isl_host_name(node_id, isl_idx)


def _isl_idx_from_ifname(ifname: str) -> int:
    """Extract ISL index from interface name. 'isl0' → 0, 'isl3' → 3."""
    return int(ifname.replace("isl", ""))


def attach_isl(
    node_id: str,
    ifname: str,
    peer_node_id: str,
    peer_ifname: str,
) -> None:
    """Activate an ISL by bringing host-side veths admin UP.

    Both pod-side interfaces are already admin UP (from wiring). tc mirred
    redirect rules were installed at wiring time and persist through state
    transitions — no mirred operations needed at runtime.

    Bringing host-side veths UP gives carrier to pod-side interfaces,
    enabling traffic flow through the pre-installed mirred rules.

    Both host-side veths must exist on this node (LOCAL ISL). For CROSS_NODE
    ISLs, the VXLAN code path handles activation separately.
    """
    host_a = _isl_host_name(node_id, _isl_idx_from_ifname(ifname))
    host_b = _isl_host_name(peer_node_id, _isl_idx_from_ifname(peer_ifname))

    ipr = IPRoute()
    try:
        for name in (host_a, host_b):
            idx = ipr.link_lookup(ifname=name)
            if not idx:
                raise FileNotFoundError(f"{name} not found in host namespace")
            ipr.link("set", index=idx[0], state="up")
    finally:
        ipr.close()

    log.debug("Attached ISL: %s <-> %s", host_a, host_b)


def detach_isl(
    node_id: str,
    ifname: str,
    peer_node_id: str,
    peer_ifname: str,
) -> None:
    """Deactivate an ISL by bringing host-side veths admin DOWN.

    tc mirred rules remain installed (they persist through state transitions
    and were set up at wiring time). Pod-side interfaces remain admin UP.
    Host-side DOWN causes carrier to drop on pod-side (LOWERLAYERDOWN),
    which is the correct idle state for a powered transceiver with no signal.
    """
    host_a = _isl_host_name(node_id, _isl_idx_from_ifname(ifname))
    host_b = _isl_host_name(peer_node_id, _isl_idx_from_ifname(peer_ifname))

    ipr = IPRoute()
    try:
        for name in (host_a, host_b):
            idx = ipr.link_lookup(ifname=name)
            if idx:
                ipr.link("set", index=idx[0], state="down")
    finally:
        ipr.close()

    log.debug("Detached ISL: %s <-> %s", host_a, host_b)


# ---------------------------------------------------------------------------
# Wiring-time infrastructure creation (moved from link_ops.py)
#
# These functions create the base veth pairs and mirred rules at session
# start. They use _in_namespace() for pod-side work (setns, not fork)
# and pyroute2 IPRoute() for host-side work. The "one jump" design
# batches all pod-side operations into a single _in_namespace call per
# endpoint to minimize _ns_lock contention.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PodVeth:
    """One pod-side interface and its host end, with the creator's decision.

    ``created`` is True when this call made the pair and False when an
    existing pair was left as found. The caller configures what was created
    and only verifies what already existed.
    """

    host_name: str
    ifname: str
    created: bool


@dataclass(frozen=True, slots=True)
class MediatedIsl:
    """Both ends of a host-mediated ISL, each with the creator's decision."""

    host_a: str
    host_b: str
    created_a: bool
    created_b: bool


def create_terminal_veth(
    node_id: str,
    pid: int,
    ifname: str,
    mtu: int | None = None,
) -> PodVeth:
    """Create the veth pair of one terminal interface OME assigns per pass. Idempotent.

    The host end (``terminal_host_name``) stays in the host namespace, admin DOWN. The pod end
    moves into the pod's namespace as ``ifname``, admin DOWN. No redirect is installed: the
    interface has no peer until its first pass.

    Returns the pair with ``created`` saying whether this call made it.
    """
    if mtu is None:
        from nodalarc.platform_config import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    host_name = terminal_host_name(node_id, ifname)

    with IPRoute() as ipr:
        if ipr.link_lookup(ifname=host_name):
            log.debug("Terminal host port %s already exists", host_name)
            return PodVeth(host_name, ifname, created=False)

        def _check_iface(ns_ipr: IPRoute) -> bool:
            return bool(ns_ipr.link_lookup(ifname=ifname))

        if _in_namespace(pid, _check_iface):
            log.debug("%s already exists in ns(%s) of %s", ifname, pid, node_id)
            return PodVeth(host_name, ifname, created=False)

        _create_veth_into_pod(ipr, host_name, ifname, pid, mtu)

    log.debug("Created terminal veth %s <-> %s in ns(%s) of %s", host_name, ifname, pid, node_id)
    return PodVeth(host_name, ifname, created=True)


@contextlib.contextmanager
def _pod_netns_fd(pid: int) -> Iterator[int]:
    """The network namespace fd of a pod, open for the duration of a proof."""
    fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    try:
        yield fd
    finally:
        os.close(fd)


def _create_veth_into_pod(
    ipr: IPRoute, host_name: str, pod_ifname: str, pid: int, mtu: int
) -> None:
    """Create a veth pair whose peer lands, named and sized, in a pod namespace.

    One RTM_NEWLINK: the host end gets ``host_name``, the peer gets
    ``pod_ifname`` inside the network namespace of ``pid``, and both ends get
    ``mtu``. The host end joins the managed device group and has IPv6 switched
    off (``switch_off_host_ipv6``). Both ends start admin DOWN. A name already
    taken on either side fails the request (EEXIST) and creates nothing.
    """
    ipr.link(
        "add",
        ifname=host_name,
        kind="veth",
        mtu=mtu,
        group=MANAGED_HOST_DEVICE_GROUP,
        peer={"ifname": pod_ifname, "net_ns_pid": pid, "mtu": mtu},
    )
    switch_off_host_ipv6(host_name)


def create_mediated_isl(
    pid_a: int,
    pid_b: int,
    ifname_a: str,
    ifname_b: str,
    node_id_a: str,
    node_id_b: str,
    mtu: int | None = None,
) -> MediatedIsl:
    """Create a host-mediated ISL: two veth pairs through the host namespace.

    Creates:
      pod-A: ifname_a <-veth-> host: _isl_{a_short}_{a_idx}
      pod-B: ifname_b <-veth-> host: _isl_{b_short}_{b_idx}

    Installs bidirectional tc mirred redirect between host-side endpoints.
    Pod-side interfaces are brought admin UP at creation (host-side stays
    DOWN, so the pod side enters LOWERLAYERDOWN until LinkUp).

    Each endpoint is under the one policy for kernel state found under a
    link's names: absent, create; complete and proven (a veth whose peer is
    the pod interface, both ends at the requested MTU), reuse with nothing
    configured; partial, conflicting or unprovable, refuse without taking
    over. Administrative state is outside the proof at both ends: LinkUp and
    LinkDown own the host end after creation and the workload owns the pod
    end.

    "One jump" design: ALL pod-side work for each endpoint is batched into
    a single _in_namespace call (rename, MTU, admin UP, MAC, IPv6 autoconfig).
    Total setns hops per ISL pair: 2 (one per endpoint).

    Returns both host names with, per endpoint, whether this call created it.
    """
    from node_agent.namespace_ops import configure_interface

    if mtu is None:
        from nodalarc.platform_config import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    host_a = _isl_host_name(node_id_a, _isl_idx_from_ifname(ifname_a))
    host_b = _isl_host_name(node_id_b, _isl_idx_from_ifname(ifname_b))
    created: dict[str, bool] = {}

    for pid, ifname, host_name, node_id in [
        (pid_a, ifname_a, host_a, node_id_a),
        (pid_b, ifname_b, host_b, node_id_b),
    ]:
        subject = f"ISL {node_id}/{ifname}"
        # The pod end is read before any host work: entering the pod
        # namespace takes the lock the host work would hold.
        pod_end = kernel_verifier.pod_veth_end(pid, ifname)
        ipr = IPRoute()
        try:
            host_idx = ipr.link_lookup(ifname=host_name)
            complete = bool(host_idx) and pod_end is not None
            proofs: tuple[kernel_verifier.Proof, ...] = ()
            if complete and pod_end is not None:
                with _pod_netns_fd(pid) as pod_ns_fd:
                    proofs = (
                        kernel_verifier.prove_veth_peer(
                            ipr, host_name, peer_ns_fd=pod_ns_fd, peer_ifindex=pod_end.ifindex
                        ),
                        kernel_verifier.prove_link_mtu(ipr, host_name, mtu=mtu),
                        kernel_verifier.prove_host_ipv6_off(host_name),
                    )
                if pod_end.kind != "veth":
                    proofs += (kernel_verifier.Proof.fail(f"pod {ifname} is not veth"),)
                if pod_end.peer_ifindex != host_idx[0]:
                    proofs += (
                        kernel_verifier.Proof.fail(
                            f"pod {ifname} peer index mismatch",
                            f"expected_ifindex={host_idx[0]}",
                            f"actual_ifindex={pod_end.peer_ifindex}",
                        ),
                    )
                if pod_end.mtu != mtu:
                    proofs += (
                        kernel_verifier.Proof.fail(
                            f"pod {ifname} MTU mismatch",
                            f"device={ifname}",
                            f"expected={mtu}",
                            f"observed={pod_end.mtu}",
                        ),
                    )
            evidence = tuple(
                item
                for item in (
                    host_name if host_idx else None,
                    f"{ifname}@pod" if pod_end is not None else None,
                )
                if item is not None
            )
            if kernel_verifier.reuse_or_refuse(
                subject=subject,
                absent=not host_idx and pod_end is None,
                complete=complete,
                proofs=proofs,
                evidence=evidence,
            ):
                log.info("Mediated ISL endpoint %s already complete and proven, reusing", subject)
                created[host_name] = False
                continue

            # Both ends absent (the policy above refused every partial state):
            # one request creates the pair with its final names, the MTU on
            # both ends, and the pod end already inside the pod namespace. The
            # host end stays admin DOWN.
            _create_veth_into_pod(ipr, host_name, ifname, pid, mtu)

            # ONE JUMP: all pod-side work in a single _in_namespace call.
            # Default args bind loop variables at definition time (B023 fix).
            def _setup_pod_side(
                ns_ipr: IPRoute,
                _if=ifname,
                _p=pid,
                _nid=node_id,
            ) -> None:
                idx = ns_ipr.link_lookup(ifname=_if)[0]
                ns_ipr.link("set", index=idx, state="up")
                configure_interface(_p, _if, _nid, ipr=ns_ipr)

            _in_namespace(pid, _setup_pod_side)
            created[host_name] = True
        finally:
            ipr.close()

    # Install bidirectional tc mirred between host-side endpoints (host ns, no setns)
    if created[host_a] and created[host_b]:
        # Devices this call created carry no qdisc: nothing to prove or reuse.
        _tc_mirred_redirect(host_a, host_b)
        _tc_mirred_redirect(host_b, host_a)
    else:
        install_redirect_pair(host_a, host_b)

    log.debug(
        "Created mediated ISL: ns(%s)/%s [%s] ↔ [%s] ns(%s)/%s (mirred installed)",
        pid_a,
        ifname_a,
        host_a,
        host_b,
        pid_b,
        ifname_b,
    )
    return MediatedIsl(host_a, host_b, created[host_a], created[host_b])
