"""Ground station bridge operations — runtime subset of link_manager.py.

Functions copied verbatim from orchestrator/link_manager.py for use
by the Node Agent DaemonSet. The originals remain in link_manager.py
for na_deploy Step 7 (deploy-time operations).

Only runtime operations are included here: attach/detach satellite to/from
GS bridge via tc mirred redirect. Deploy-time operations (create_ground_bridge,
create_satellite_ground_veth) stay in link_manager.py.

All tc mirred operations run in the host network namespace, which the
DaemonSet has direct access to (hostNetwork: true).
"""

from __future__ import annotations

import logging
import subprocess

from pyroute2 import IPRoute, NetNS

log = logging.getLogger(__name__)


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
    """Install tc ingress + mirred egress redirect from src to dst."""
    # Remove stale ingress qdisc and filters (idempotent).
    # Must succeed before adding new ingress — the kernel's exclusivity
    # flag prevents adding a second ingress qdisc.
    subprocess.run(
        ["tc", "qdisc", "del", "dev", src, "ingress"],
        capture_output=True,
    )
    result = subprocess.run(
        ["tc", "qdisc", "add", "dev", src, "ingress"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # "Exclusivity flag on" means ingress already exists — retry delete+add
        if "Exclusivity" in result.stderr or "File exists" in result.stderr:
            subprocess.run(["tc", "qdisc", "del", "dev", src, "ingress"], capture_output=True)
            subprocess.run(
                ["tc", "qdisc", "add", "dev", src, "ingress"],
                capture_output=True,
                check=True,
            )
        else:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stderr)
    subprocess.run(
        [
            "tc",
            "filter",
            "add",
            "dev",
            src,
            "parent",
            "ffff:",
            "protocol",
            "all",
            "u32",
            "match",
            "u32",
            "0",
            "0",
            "action",
            "mirred",
            "egress",
            "redirect",
            "dev",
            dst,
        ],
        capture_output=True,
        check=True,
    )


def _tc_mirred_remove(ifname: str) -> None:
    """Remove tc ingress qdisc (and all its filters) from an interface."""
    subprocess.run(
        ["tc", "qdisc", "del", "dev", ifname, "ingress"],
        capture_output=True,
    )


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
    """
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
    ns = NetNS(f"/proc/{sat_pid}/ns/net")
    try:
        gnd_idx = ns.link_lookup(ifname="gnd0")
        if not gnd_idx:
            raise FileNotFoundError(f"gnd0 not found in sat ns({sat_pid})")
        ns.link("set", index=gnd_idx[0], state="up")
    finally:
        ns.close()

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
    """
    gs_port = _gs_bridge_port_name(gs_id)
    host_veth = _sat_gnd_host_name(sat_id)

    # Remove tc redirect first
    _tc_mirred_remove(gs_port)
    _tc_mirred_remove(host_veth)

    # Bring satellite gnd0 DOWN
    ns = NetNS(f"/proc/{sat_pid}/ns/net")
    try:
        gnd_idx = ns.link_lookup(ifname="gnd0")
        if gnd_idx:
            ns.link("set", index=gnd_idx[0], state="down")
    finally:
        ns.close()

    # Bring host veth DOWN
    ipr = IPRoute()
    try:
        host_idx = ipr.link_lookup(ifname=host_veth)
        if host_idx:
            ipr.link("set", index=host_idx[0], state="down")
    finally:
        ipr.close()

    log.info(f"Detached {sat_id} from {gs_id}")
