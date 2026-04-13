# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Ground station and ISL link infrastructure — wiring + runtime operations.

All host-mediated veth creation (ISL and ground) and tc mirred redirect
management. Used by both wiring.py (deploy-time) and handlers.py (runtime).

tc mirred operations use pyroute2 native netlink tc calls (no subprocess).
Namespace operations use _in_namespace() from namespace_ops.py (setns, no fork).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections import defaultdict

from pyroute2 import IPRoute

from node_agent.namespace_ops import _in_namespace

log = logging.getLogger(__name__)

# Per-ground-station lock. Serializes attach/detach operations on the same
# GS bridge port (_gbr-{gs}). Prevents the TOCTOU race where a concurrent
# detach deletes the ingress qdisc that an attach just created.
# defaultdict creates a new Lock for each GS on first access.
_gs_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


# ---------------------------------------------------------------------------
# Naming helpers (link_manager.py L31-58)
# ---------------------------------------------------------------------------


def _sat_short_id(sat_id: str) -> str:
    """Stable short identifier from satellite ID.

    "sat-P00S05" -> "P00S05"
    """
    if sat_id.startswith("sat-"):
        return sat_id[4:]
    return sat_id[-10:]


def _gs_short_name(gs_id: str) -> str:
    """Extract station name from gs_id, stripping 'gs-' prefix."""
    return gs_id[3:] if gs_id.startswith("gs-") else gs_id


def _gs_bridge_port_name(gs_id: str) -> str:
    """Host-side veth name for GS bridge port. <=15 chars."""
    return f"_gbr-{_gs_short_name(gs_id)}"[:15]


def _sat_gnd_host_name(sat_id: str) -> str:
    """Host-side veth name for satellite ground link. <=15 chars."""
    return f"_gnd_{_sat_short_id(sat_id)}"[:15]


# ---------------------------------------------------------------------------
# TC mirred redirect (link_manager.py L734-779)
# ---------------------------------------------------------------------------


def _tc_mirred_redirect(src: str, dst: str) -> None:
    """Install tc ingress + mirred egress redirect from src to dst.

    Uses pyroute2 native netlink tc calls. No subprocess, no fork.
    Benchmarked: 1.97ms vs 31ms per install (16x faster).

    Operates in the host network namespace (no setns needed).
    """
    ipr = IPRoute()
    try:
        src_idx = ipr.link_lookup(ifname=src)
        if not src_idx:
            raise FileNotFoundError(f"tc mirred: source interface {src} not found")
        dst_idx = ipr.link_lookup(ifname=dst)
        if not dst_idx:
            raise FileNotFoundError(f"tc mirred: destination interface {dst} not found")

        # Delete stale ingress qdisc (idempotent)
        with contextlib.suppress(Exception):
            ipr.tc("del", index=src_idx[0], kind="ingress")

        # Add ingress qdisc
        ipr.tc("add", index=src_idx[0], kind="ingress")

        # Add u32 match-all filter with mirred egress redirect action
        try:
            ipr.tc(
                "add-filter",
                kind="u32",
                index=src_idx[0],
                parent=0xFFFF0000,
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


# ---------------------------------------------------------------------------
# Attach / detach (link_manager.py L782-858)
# ---------------------------------------------------------------------------


def attach_to_ground_bridge(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    """Connect satellite to GS via tc mirred redirect.

    Brings both host-side veths and satellite gnd0 admin UP, then
    installs bidirectional tc mirred redirect between the GS and
    satellite host-side veths.

    Serialized per GS: concurrent attach/detach on the same GS bridge
    port would corrupt the tc ingress qdisc (TOCTOU race). The per-GS
    lock prevents this.
    """
    with _gs_locks[gs_id]:
        _attach_to_ground_bridge_unlocked(gs_id, sat_id, sat_pid)


def _attach_to_ground_bridge_unlocked(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    gs_port = _gs_bridge_port_name(gs_id)
    host_veth = _sat_gnd_host_name(sat_id)

    ipr = IPRoute()
    try:
        for name in (gs_port, host_veth):
            idx = ipr.link_lookup(ifname=name)
            if not idx:
                raise FileNotFoundError(f"{name} not found")
            ipr.link("set", index=idx[0], state="up")
    finally:
        ipr.close()

    # Bring satellite gnd0 UP
    def _up_gnd0(ipr: IPRoute) -> None:
        gnd_idx = ipr.link_lookup(ifname="gnd0")
        if not gnd_idx:
            raise FileNotFoundError(f"gnd0 not found in sat ns({sat_pid})")
        ipr.link("set", index=gnd_idx[0], state="up")

    _in_namespace(sat_pid, _up_gnd0)

    # Bidirectional tc mirred redirect between host-side veths
    _tc_mirred_redirect(gs_port, host_veth)
    _tc_mirred_redirect(host_veth, gs_port)

    log.info(f"Attached {sat_id} to {gs_id} (tc redirect)")


def detach_from_ground_bridge(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    """Disconnect satellite from GS.

    Removes tc mirred redirect, then brings satellite gnd0 and
    host veth admin DOWN.

    Serialized per GS: see attach_to_ground_bridge.
    """
    with _gs_locks[gs_id]:
        _detach_from_ground_bridge_unlocked(gs_id, sat_id, sat_pid)


def _detach_from_ground_bridge_unlocked(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    gs_port = _gs_bridge_port_name(gs_id)
    host_veth = _sat_gnd_host_name(sat_id)

    # Remove tc redirect first
    _tc_mirred_remove(gs_port)
    _tc_mirred_remove(host_veth)

    # Bring satellite gnd0 DOWN
    def _down_gnd0(ipr: IPRoute) -> None:
        gnd_idx = ipr.link_lookup(ifname="gnd0")
        if gnd_idx:
            ipr.link("set", index=gnd_idx[0], state="down")

    _in_namespace(sat_pid, _down_gnd0)

    # Bring satellite host veth and GS bridge port DOWN.
    # GS bridge port DOWN drops carrier on gnd0 inside the GS pod
    # (gnd0 transitions UP → LOWERLAYERDOWN), triggering immediate
    # FRR adjacency teardown. Matches attach_to_ground_bridge which
    # brings both UP.
    ipr = IPRoute()
    try:
        for name in (host_veth, gs_port):
            idx = ipr.link_lookup(ifname=name)
            if idx:
                ipr.link("set", index=idx[0], state="down")
    finally:
        ipr.close()

    log.info(f"Detached {sat_id} from {gs_id}")


# ---------------------------------------------------------------------------
# ISL host-mediated attach / detach
# ---------------------------------------------------------------------------


def _isl_host_name(node_id: str, isl_idx: int) -> str:
    """Host-side veth name for ISL endpoint. ≤15 chars."""
    return f"_isl_{_sat_short_id(node_id)}_{isl_idx}"[:15]


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

    log.info(f"Attached ISL: {host_a} <-> {host_b}")


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

    log.info(f"Detached ISL: {host_a} <-> {host_b}")


# ---------------------------------------------------------------------------
# Wiring-time infrastructure creation (moved from link_ops.py)
#
# These functions create the base veth pairs and mirred rules at session
# start. They use _in_namespace() for pod-side work (setns, not fork)
# and pyroute2 IPRoute() for host-side work. The "one jump" design
# batches all pod-side operations into a single _in_namespace call per
# endpoint to minimize _ns_lock contention.
# ---------------------------------------------------------------------------


def create_ground_bridge(
    gs_id: str,
    gs_pid: int,
    mtu: int | None = None,
) -> str:
    """Create GS-side veth pair for ground link. Idempotent.

    Creates a veth pair: host end (_gbr-{gs}) stays in host ns (DOWN),
    GS end moved into GS namespace as gnd0 (DOWN).

    Returns host-side veth name.
    """
    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    gs_port = _gs_bridge_port_name(gs_id)

    ipr = IPRoute()
    try:
        # Idempotent: skip if host port already exists
        if ipr.link_lookup(ifname=gs_port):
            log.debug(f"GS port {gs_port} already exists")
            return gs_port

        # Check if gnd0 already exists in GS namespace
        def _check_gnd0(ns_ipr: IPRoute) -> bool:
            return bool(ns_ipr.link_lookup(ifname="gnd0"))

        if _in_namespace(gs_pid, _check_gnd0):
            log.debug(f"gnd0 already exists in GS ns({gs_pid})")
            return gs_port

        # Create veth pair with temp names
        rand = os.urandom(3).hex()
        tmp_host = f"_na_h{rand}"[:15]
        tmp_ns = f"_na_n{rand}"[:15]

        for tmp in [tmp_host, tmp_ns]:
            stale = ipr.link_lookup(ifname=tmp)
            if stale:
                ipr.link("del", index=stale[0])

        ipr.link("add", ifname=tmp_host, peer={"ifname": tmp_ns}, kind="veth")

        # Host end: rename, set MTU — leave DOWN
        host_idx = ipr.link_lookup(ifname=tmp_host)[0]
        ipr.link("set", index=host_idx, ifname=gs_port, mtu=mtu)

        # Move NS end into GS namespace
        ns_idx = ipr.link_lookup(ifname=tmp_ns)[0]
        ipr.link("set", index=ns_idx, net_ns_pid=gs_pid)

        # ONE JUMP: rename to gnd0 inside GS namespace, leave DOWN
        _tmp_ns = tmp_ns  # capture for closure

        def _rename_gnd0(ns_ipr: IPRoute) -> None:
            idx = ns_ipr.link_lookup(ifname=_tmp_ns)[0]
            ns_ipr.link("set", index=idx, ifname="gnd0", mtu=mtu)

        _in_namespace(gs_pid, _rename_gnd0)

        log.info(f"Created GS port {gs_port} → gnd0 in ns({gs_pid})")
    finally:
        ipr.close()

    return gs_port


def create_satellite_ground_veth(
    sat_id: str,
    sat_pid: int,
    mtu: int | None = None,
) -> tuple[str, str]:
    """Pre-create satellite ground veth pair at deploy time. Idempotent.

    Returns (host_side_name, "gnd0").
    """
    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    host_name = _sat_gnd_host_name(sat_id)

    ipr = IPRoute()
    try:
        if ipr.link_lookup(ifname=host_name):
            log.debug(f"Satellite ground veth {host_name} already exists")
            return (host_name, "gnd0")

        def _check_gnd0(ns_ipr: IPRoute) -> bool:
            return bool(ns_ipr.link_lookup(ifname="gnd0"))

        if _in_namespace(sat_pid, _check_gnd0):
            log.debug(f"gnd0 already exists in sat ns({sat_pid})")
            return (host_name, "gnd0")

        rand = os.urandom(3).hex()
        tmp_host = f"_na_h{rand}"[:15]
        tmp_ns = f"_na_n{rand}"[:15]

        for tmp in [tmp_host, tmp_ns]:
            stale = ipr.link_lookup(ifname=tmp)
            if stale:
                ipr.link("del", index=stale[0])

        ipr.link("add", ifname=tmp_host, peer={"ifname": tmp_ns}, kind="veth")

        host_idx = ipr.link_lookup(ifname=tmp_host)[0]
        ipr.link("set", index=host_idx, ifname=host_name, mtu=mtu)

        ns_idx = ipr.link_lookup(ifname=tmp_ns)[0]
        ipr.link("set", index=ns_idx, net_ns_pid=sat_pid)

        _tmp_ns = tmp_ns

        def _rename_gnd0(ns_ipr: IPRoute) -> None:
            idx = ns_ipr.link_lookup(ifname=_tmp_ns)[0]
            ns_ipr.link("set", index=idx, ifname="gnd0", mtu=mtu)

        _in_namespace(sat_pid, _rename_gnd0)
    finally:
        ipr.close()

    log.info(f"Created satellite ground veth {host_name} ↔ gnd0 in ns({sat_pid})")
    return (host_name, "gnd0")


def create_mediated_isl(
    pid_a: int,
    pid_b: int,
    ifname_a: str,
    ifname_b: str,
    node_id_a: str,
    node_id_b: str,
    mtu: int | None = None,
) -> tuple[str, str]:
    """Create a host-mediated ISL: two veth pairs through the host namespace.

    Creates:
      pod-A: ifname_a ←veth→ host: _isl_{a_short}_{a_idx}
      pod-B: ifname_b ←veth→ host: _isl_{b_short}_{b_idx}

    Installs bidirectional tc mirred redirect between host-side endpoints.
    Pod-side interfaces are brought admin UP immediately (host-side stays
    DOWN → pod-side enters LOWERLAYERDOWN).

    "One jump" design: ALL pod-side work for each endpoint is batched into
    a single _in_namespace call (rename, MTU, admin UP, MAC, IPv6 autoconfig).
    Total setns hops per ISL pair: 2 (one per endpoint).

    Returns (host_name_a, host_name_b).
    """
    from node_agent.namespace_ops import configure_interface

    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    host_a = _isl_host_name(node_id_a, _isl_idx_from_ifname(ifname_a))
    host_b = _isl_host_name(node_id_b, _isl_idx_from_ifname(ifname_b))

    for pid, ifname, host_name, node_id in [
        (pid_a, ifname_a, host_a, node_id_a),
        (pid_b, ifname_b, host_b, node_id_b),
    ]:
        ipr = IPRoute()
        try:
            # Idempotent: skip if host side already exists
            if ipr.link_lookup(ifname=host_name):
                log.debug(f"ISL host veth {host_name} already exists, skipping")
                continue

            # Create veth pair with temp names in host namespace
            rand = os.urandom(3).hex()
            tmp_host = f"_na_h{rand}"[:15]
            tmp_ns = f"_na_n{rand}"[:15]

            for tmp in [tmp_host, tmp_ns]:
                stale = ipr.link_lookup(ifname=tmp)
                if stale:
                    ipr.link("del", index=stale[0])

            ipr.link("add", ifname=tmp_host, peer={"ifname": tmp_ns}, kind="veth")

            # Host end: rename, set MTU — leave admin DOWN
            host_idx = ipr.link_lookup(ifname=tmp_host)[0]
            ipr.link("set", index=host_idx, ifname=host_name, mtu=mtu)

            # Move pod end into target namespace
            ns_idx = ipr.link_lookup(ifname=tmp_ns)[0]
            ipr.link("set", index=ns_idx, net_ns_pid=pid)

            # ONE JUMP: all pod-side work in a single _in_namespace call.
            # Default args bind loop variables at definition time (B023 fix).
            def _setup_pod_side(
                ns_ipr: IPRoute,
                _tmp=tmp_ns,
                _if=ifname,
                _m=mtu,
                _p=pid,
                _nid=node_id,
            ) -> None:
                idx = ns_ipr.link_lookup(ifname=_tmp)[0]
                ns_ipr.link("set", index=idx, ifname=_if, mtu=_m, state="up")
                configure_interface(_p, _if, _nid, ipr=ns_ipr)

            _in_namespace(pid, _setup_pod_side)
        finally:
            ipr.close()

    # Install bidirectional tc mirred between host-side endpoints (host ns, no setns)
    _tc_mirred_redirect(host_a, host_b)
    _tc_mirred_redirect(host_b, host_a)

    log.info(
        f"Created mediated ISL: ns({pid_a})/{ifname_a} [{host_a}] "
        f"↔ [{host_b}] ns({pid_b})/{ifname_b} (mirred installed)"
    )
    return (host_a, host_b)
