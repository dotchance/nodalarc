"""Namespace netlink operations — runtime subset of link_manager.py.

Functions copied verbatim from orchestrator/link_manager.py for use
by the Node Agent DaemonSet. The originals remain in link_manager.py
for na_deploy Step 7 (deploy-time operations).

Only runtime operations are included here: admin up/down, tc shaping,
NDP resolution. Deploy-time operations (create_veth_pair, create_ground_bridge,
create_satellite_ground_veth, etc.) stay in link_manager.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import subprocess

from pyroute2 import NetNS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MAC / link-local helpers (link_manager.py L234-254, L357-364)
# ---------------------------------------------------------------------------


def mac_to_link_local(mac_str: str) -> str:
    """Derive canonical IPv6 link-local from MAC via EUI-64.

    '02:ee:d2:0a:a9:36' -> 'fe80::ee:d2ff:fe0a:a936'

    Used to compute the peer's link-local address for NDP solicitation
    and for ``via inet6`` in MPLS routes.
    """
    import ipaddress

    parts = [int(x, 16) for x in mac_str.split(":")]
    parts[0] ^= 0x02  # flip U/L bit
    eui64 = parts[:3] + [0xFF, 0xFE] + parts[3:]
    groups = [
        f"{eui64[0]:02x}{eui64[1]:02x}",
        f"{eui64[2]:02x}{eui64[3]:02x}",
        f"{eui64[4]:02x}{eui64[5]:02x}",
        f"{eui64[6]:02x}{eui64[7]:02x}",
    ]
    raw = "fe80::" + ":".join(groups)
    return str(ipaddress.IPv6Address(raw))


def deterministic_mac(node_id: str, ifname: str) -> str:
    """Derive a deterministic locally-administered unicast MAC address.

    Format: 02:XX:XX:XX:XX:XX where XX bytes come from SHA-256 of
    node_id + ifname. The 02 prefix sets the locally-administered bit.
    """
    digest = hashlib.sha256(f"{node_id}:{ifname}".encode()).digest()
    return f"02:{digest[0]:02x}:{digest[1]:02x}:{digest[2]:02x}:{digest[3]:02x}:{digest[4]:02x}"


# ---------------------------------------------------------------------------
# Interface admin state (link_manager.py L423-444)
# ---------------------------------------------------------------------------


def set_interface_up(pid: int, ifname: str) -> None:
    """Bring an interface up inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.link("set", index=links[0], state="up")
    finally:
        ns.close()


def disable_dad(pid: int, ifname: str) -> None:
    """Disable IPv6 DAD on an interface to avoid ~1s tentative delay.

    When an interface goes admin UP, the kernel runs Duplicate Address
    Detection on the link-local address. This takes ~1 second (one NS
    probe + retransmit timer). During DAD the address is tentative and
    NDP resolution from peers will fail.

    ISL and ground interfaces use deterministic MACs, so DAD is unnecessary.
    Disabling it allows NDP to resolve in <100ms instead of >1000ms.
    """
    subprocess.run(
        [
            "nsenter",
            "--target",
            str(pid),
            "--net",
            "--",
            "sysctl",
            "-w",
            f"net.ipv6.conf.{ifname}.dad_transmits=0",
        ],
        capture_output=True,
        text=True,
    )


def set_interface_down(pid: int, ifname: str) -> None:
    """Bring an interface down inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.link("set", index=links[0], state="down")
    finally:
        ns.close()


# ---------------------------------------------------------------------------
# TC shaping (link_manager.py L447-510, L567-576)
# ---------------------------------------------------------------------------


def apply_link_shaping(
    pid: int,
    ifname: str,
    delay_ms: float,
    rate_mbps: float,
) -> None:
    """Apply tc tbf root + netem child for bandwidth and delay.

    Called once when a link goes up. Subsequent delay changes use
    update_delay().

    PRD Appendix A: tbf root qdisc, netem child.
    """
    rate_bps = int(rate_mbps * 1_000_000)
    # tbf burst: at least 1 MTU, typically rate / 250 Hz
    burst = max(9000, rate_bps // 250)
    # tbf latency: buffer time in microseconds
    latency_us = 50000  # 50ms buffer
    delay_us = int(delay_ms * 1000)

    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        # Remove existing qdiscs (idempotent)
        with contextlib.suppress(Exception):
            ns.tc("del", index=idx, root=True)
        # Root: tbf for bandwidth shaping (handle 1:0)
        ns.tc(
            "add",
            kind="tbf",
            index=idx,
            handle=0x00010000,
            rate=rate_bps,
            burst=burst,
            latency=latency_us,
        )
        # Child: netem for delay (under class 1:1)
        ns.tc("add", kind="netem", index=idx, handle=0x00100000, parent=0x00010001, delay=delay_us)
    finally:
        ns.close()
    log.info(f"Applied shaping on ns({pid})/{ifname}: {delay_ms}ms, {rate_mbps}Mbps")


def update_delay(pid: int, ifname: str, delay_ms: float) -> None:
    """Update netem delay on an existing qdisc chain.

    Uses tc "change" — NOT "add" or "replace". The qdisc must already
    exist from apply_link_shaping(). This only modifies the delay parameter.
    """
    delay_us = int(delay_ms * 1000)
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.tc(
            "change",
            kind="netem",
            index=links[0],
            handle=0x00100000,
            parent=0x00010001,
            delay=delay_us,
        )
    finally:
        ns.close()


def remove_link_shaping(pid: int, ifname: str) -> None:
    """Remove all tc qdiscs from an interface."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.tc("del", index=idx, root=True)
    except Exception:
        pass  # Interface may already be gone or no qdisc set
    finally:
        ns.close()


# ---------------------------------------------------------------------------
# NDP resolution (link_manager.py L257-354)
# ---------------------------------------------------------------------------


def trigger_ndp_and_wait(pid: int, ifname: str, peer_ll: str, timeout_ms: int = 1500) -> bool:
    """Trigger NDP solicitation and wait for the peer to become REACHABLE.

    After the TO brings an ISL or ground interface admin UP, call this
    before emitting LinkUp. This ensures the kernel's neighbor table has
    a resolved L2 entry for the peer's link-local, so MPLS routes with
    ``via inet6 <peer_ll>`` can forward immediately.

    Uses a UDP6 connect() to trigger the kernel to send a Neighbor
    Solicitation, then polls the neighbor table until REACHABLE or STALE.

    Returns True if resolved within timeout, False otherwise.
    On timeout, logs a warning — the sidecar's retry mechanism handles it.
    """
    import time as _time

    ns_path = f"/proc/{pid}/ns/net"
    ns = NetNS(ns_path)
    try:
        iface_links = ns.link_lookup(ifname=ifname)
        if not iface_links:
            log.warning("NDP: interface %s not found in ns(%d)", ifname, pid)
            return False
        iface_idx = iface_links[0]
    finally:
        ns.close()

    # Trigger NS by pinging the peer's link-local with %scope format.
    # The %ifname suffix is required for link-local addresses to bind to
    # the correct interface. Without it, the kernel may send the NS from
    # a different interface and resolve the wrong neighbor.
    subprocess.run(
        [
            "nsenter",
            "--target",
            str(pid),
            "--net",
            "--",
            "ping",
            "-6",
            "-c",
            "1",
            "-W",
            "2",
            f"{peer_ll}%{ifname}",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Poll neighbor table
    start = _time.monotonic()
    deadline = start + (timeout_ms / 1000)
    NUD_REACHABLE = 0x02
    NUD_STALE = 0x04
    NUD_FAILED = 0x20

    while _time.monotonic() < deadline:
        ns = NetNS(ns_path)
        try:
            neighbours = ns.get_neighbours(family=10, ifindex=iface_idx)  # AF_INET6=10
            for n in neighbours:
                attrs = dict(n["attrs"])
                if attrs.get("NDA_DST") == peer_ll:
                    state = n["state"]
                    elapsed = (_time.monotonic() - start) * 1000
                    if state & (NUD_REACHABLE | NUD_STALE):
                        log.debug(
                            "NDP resolved %s on %s in ns(%d) in %.1fms",
                            peer_ll,
                            ifname,
                            pid,
                            elapsed,
                        )
                        return True
                    if state & NUD_FAILED:
                        log.error(
                            "NDP FAILED for %s on %s in ns(%d) after %.1fms",
                            peer_ll,
                            ifname,
                            pid,
                            elapsed,
                        )
                        return False
        finally:
            ns.close()
        _time.sleep(0.010)  # 10ms poll

    elapsed = (_time.monotonic() - start) * 1000
    log.warning(
        "NDP timeout for %s on %s in ns(%d) after %.1fms — proceeding, sidecar retry will catch it",
        peer_ll,
        ifname,
        pid,
        elapsed,
    )
    return False
