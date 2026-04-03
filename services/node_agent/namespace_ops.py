# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Namespace netlink operations — runtime subset of link_manager.py.

All namespace entry uses _in_namespace() which calls setns() directly
instead of pyroute2's NetNS(). This avoids fork() in a multi-threaded
process — see docs/node-agent-fork-issue.md for the full analysis.

Never use pyroute2 NetNS() directly in the Node Agent.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import logging
import os
import subprocess
import threading
from collections.abc import Callable
from typing import TypeVar

from pyroute2 import IPRoute

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_CLONE_NEWNET = 0x40000000

# Host network namespace fd — opened lazily on first use.
# With hostPID:true, PID 1 is the host's init process.
_HOST_NS_FD: int | None = None
_host_ns_lock = threading.Lock()

# Thread lock: setns changes the calling thread's namespace.
# Concurrent setns calls from different threads would race.
_ns_lock = threading.Lock()


def _get_host_ns_fd() -> int:
    """Get the host namespace fd, opening it on first call."""
    global _HOST_NS_FD
    if _HOST_NS_FD is not None:
        return _HOST_NS_FD
    with _host_ns_lock:
        if _HOST_NS_FD is not None:
            return _HOST_NS_FD
        _HOST_NS_FD = os.open("/proc/1/ns/net", os.O_RDONLY)
        return _HOST_NS_FD


def _in_namespace(pid: int, fn: Callable[[IPRoute], _T]) -> _T:
    """Execute fn(ipr) inside the network namespace of the given PID.

    Uses setns() syscall to enter the namespace in the current thread,
    runs the callable with a fresh IPRoute instance, then returns to
    the host namespace. Thread-safe via _ns_lock.

    This replaces pyroute2's NetNS() which forks a child process —
    the fork inherits signal handlers and causes the orphaned-child
    problem documented in docs/node-agent-fork-issue.md.
    """
    target_fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    try:
        with _ns_lock:
            ret = _libc.setns(target_fd, _CLONE_NEWNET)
            if ret != 0:
                errno = ctypes.get_errno()
                raise OSError(errno, f"setns to ns({pid}) failed: {os.strerror(errno)}")
            try:
                ipr = IPRoute()
                try:
                    return fn(ipr)
                finally:
                    ipr.close()
            finally:
                ret = _libc.setns(_get_host_ns_fd(), _CLONE_NEWNET)
                if ret != 0:
                    errno = ctypes.get_errno()
                    log.error("setns back to host failed: %s", os.strerror(errno))
    finally:
        os.close(target_fd)


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

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ipr.link("set", index=links[0], state="up")

    _in_namespace(pid, _op)


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

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ipr.link("set", index=links[0], state="down")

    _in_namespace(pid, _op)


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

    Uses tbf root qdisc with netem child for combined shaping.
    """
    rate_bps = int(rate_mbps * 1_000_000)
    burst = max(9000, rate_bps // 250)
    latency_us = 50000  # 50ms buffer
    delay_us = int(delay_ms * 1000)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        with contextlib.suppress(Exception):
            ipr.tc("del", index=idx, root=True)
        ipr.tc(
            "add",
            kind="tbf",
            index=idx,
            handle=0x00010000,
            rate=rate_bps,
            burst=burst,
            latency=latency_us,
        )
        ipr.tc("add", kind="netem", index=idx, handle=0x00100000, parent=0x00010001, delay=delay_us)

    _in_namespace(pid, _op)
    log.info(f"Applied shaping on ns({pid})/{ifname}: {delay_ms}ms, {rate_mbps}Mbps")


def update_delay(pid: int, ifname: str, delay_ms: float) -> None:
    """Update netem delay on an existing qdisc chain.

    Uses tc "change" — NOT "add" or "replace". The qdisc must already
    exist from apply_link_shaping(). This only modifies the delay parameter.
    """
    delay_us = int(delay_ms * 1000)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ipr.tc(
            "change",
            kind="netem",
            index=links[0],
            handle=0x00100000,
            parent=0x00010001,
            delay=delay_us,
        )

    _in_namespace(pid, _op)


def remove_link_shaping(pid: int, ifname: str) -> None:
    """Remove all tc qdiscs from an interface."""

    def _op(ipr: IPRoute) -> None:
        idx = ipr.link_lookup(ifname=ifname)[0]
        ipr.tc("del", index=idx, root=True)

    with contextlib.suppress(Exception):
        _in_namespace(pid, _op)


# ---------------------------------------------------------------------------
# NDP resolution (link_manager.py L257-354)
# ---------------------------------------------------------------------------


def trigger_ndp_and_wait(pid: int, ifname: str, peer_ll: str, timeout_ms: int = 1500) -> bool:
    """Trigger NDP solicitation and wait for the peer to become REACHABLE.

    After the TO brings an ISL or ground interface admin UP, call this
    before emitting LinkUp. This ensures the kernel's neighbor table has
    a resolved L2 entry for the peer's link-local, so MPLS routes with
    ``via inet6 <peer_ll>`` can forward immediately.

    Returns True if resolved within timeout, False otherwise.
    """
    import time as _time

    # Get interface index
    def _get_iface_idx(ipr: IPRoute) -> int:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            return -1
        return links[0]

    iface_idx = _in_namespace(pid, _get_iface_idx)
    if iface_idx < 0:
        log.warning("NDP: interface %s not found in ns(%d)", ifname, pid)
        return False

    # Trigger NS by pinging the peer's link-local via nsenter.
    # nsenter is a separate process — it doesn't fork from our process.
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

        def _check_neighbors(ipr: IPRoute) -> int | None:
            """Returns NUD state if peer found, None otherwise."""
            for n in ipr.get_neighbours(family=10, ifindex=iface_idx):
                attrs = dict(n["attrs"])
                if attrs.get("NDA_DST") == peer_ll:
                    return n["state"]
            return None

        state = _in_namespace(pid, _check_neighbors)
        if state is not None:
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
        _time.sleep(0.010)

    elapsed = (_time.monotonic() - start) * 1000
    log.warning(
        "NDP timeout for %s on %s in ns(%d) after %.1fms — proceeding, sidecar retry will catch it",
        peer_ll,
        ifname,
        pid,
        elapsed,
    )
    return False
