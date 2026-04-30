# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Namespace netlink operations — runtime subset of link_manager.py.

All namespace entry uses _in_namespace() which calls setns() directly
instead of pyroute2's NetNS(). This avoids fork() in a multi-threaded
process — see docs/node-agent-fork-issue.md for the full analysis.

Never use pyroute2 NetNS() directly in the Node Agent.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
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
# MAC helper (link_manager.py L357-364)
# ---------------------------------------------------------------------------
# `mac_to_link_local` (EUI-64 derivation of IPv6 link-local from MAC) was
# removed with the v0.72 NDP deletion. The Node Agent does not do L3 work.
# The `nodalpath-fwd` sidecar carries its own copy at
# `nodalpath/push/grpc_push.py:_mac_to_link_local` for `via inet6` route
# construction.


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

    Strictly idempotent via NLM_F_REPLACE | NLM_F_CREATE: creates the
    qdisc if it doesn't exist, replaces it in-place if it does. No
    need to delete first, no race window, safe to call regardless of
    prior interface state (fresh, previously shaped, or orphaned from
    a prior LinkDown that skipped shaping removal).

    Deterministic handle hierarchy:
      TBF root:   handle 0x00010000  (1:0 in tc notation)
      netem child: handle 0x00100000  (16:0) parent 0x00010001 (1:1)

    Called on LinkUp. Subsequent delay-only changes use update_delay().
    """
    rate_bps = int(rate_mbps * 1_000_000)
    if rate_bps > 0xFFFFFFFF:
        rate_bps = 0xFFFFFFFF
    burst = max(9000, rate_bps // 250)
    if burst > 0xFFFFFFFF:
        burst = 0xFFFFFFFF
    latency_us = 50000  # 50ms buffer
    delay_us = int(delay_ms * 1000)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        ipr.tc(
            "replace",
            kind="tbf",
            index=idx,
            handle=0x00010000,
            rate=rate_bps,
            burst=burst,
            latency=latency_us,
        )
        ipr.tc(
            "replace", kind="netem", index=idx, handle=0x00100000, parent=0x00010001, delay=delay_us
        )

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


# ---------------------------------------------------------------------------
# Wiring-time operations (moved from link_ops.py, rewritten to use setns)
# ---------------------------------------------------------------------------


def _write_sysctl_in_netns(
    pid: int, sysctl_key: str, value: str, already_in_ns: bool = False
) -> str | None:
    """Write a sysctl value inside a network namespace.

    Uses _in_namespace() with setns() instead of spawning a throwaway
    thread. Returns None on success, error string on failure.

    When already_in_ns=True, writes directly to /proc/sys (caller is
    already inside the correct namespace via _in_namespace). This
    prevents _ns_lock deadlock — threading.Lock is not reentrant.
    """
    from pathlib import Path

    def _do_write(_ipr: IPRoute) -> None:
        sysctl_path = Path("/proc/sys") / sysctl_key.replace(".", "/")
        sysctl_path.write_text(str(value))

    try:
        if already_in_ns:
            _do_write(None)
        else:
            _in_namespace(pid, _do_write)
        return None
    except Exception as exc:
        return str(exc)


def disable_ipv6_autoconfig(pid: int, ifname: str, ipr: IPRoute | None = None) -> None:
    """Disable IPv6 autoconfig on an interface inside a namespace.

    If ipr is provided, we're already inside _in_namespace — write
    sysctls directly with already_in_ns=True to avoid deadlock.
    """
    in_ns = ipr is not None
    for param in ("accept_ra", "autoconf"):
        err = _write_sysctl_in_netns(
            pid, f"net.ipv6.conf.{ifname}.{param}", "0", already_in_ns=in_ns
        )
        if err:
            log.warning("Failed to set %s=0 for %s in ns(%d): %s", param, ifname, pid, err)


def configure_interface(pid: int, ifname: str, node_id: str, ipr: IPRoute | None = None) -> None:
    """Apply post-creation configuration to an interface in a namespace.

    Disables IPv6 autoconfig and sets a deterministic MAC address.
    MTU is set during veth creation, not here.

    If ipr is provided, uses the handle directly (already inside
    _in_namespace — zero additional setns hops). If not, wraps all
    work in one _in_namespace call.
    """
    mac = deterministic_mac(node_id, ifname)

    def _do_configure(handle: IPRoute) -> None:
        idx = handle.link_lookup(ifname=ifname)
        if not idx:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        handle.link("set", index=idx[0], address=mac)
        # Disable IPv6 autoconfig — already in namespace, pass handle
        disable_ipv6_autoconfig(pid, ifname, ipr=handle)

    if ipr is not None:
        _do_configure(ipr)
    else:
        _in_namespace(pid, _do_configure)
    log.debug("Configured %s in ns(%d): mac=%s, ipv6_autoconfig=off", ifname, pid, mac)


def create_dummy_interface(pid: int, ifname: str, addresses: list[str]) -> None:
    """Create a dummy interface inside a namespace with given addresses.

    Used for terrestrial prefix interfaces (terr0) on ground station pods.
    Idempotent — if the interface already exists (e.g., FRR zebra created
    it from frr.conf), verifies it's UP and skips creation.
    """

    def _op(ipr: IPRoute) -> None:
        existing = ipr.link_lookup(ifname=ifname)
        if existing:
            log.debug(
                "dummy %s already exists in ns(%d) idx=%d, ensuring UP", ifname, pid, existing[0]
            )
            ipr.link("set", index=existing[0], state="up")
            return
        log.debug("dummy %s not found in ns(%d), creating", ifname, pid)
        ipr.link("add", ifname=ifname, kind="dummy")
        idx = ipr.link_lookup(ifname=ifname)[0]
        ipr.link("set", index=idx, state="up")
        for addr in addresses:
            ip_addr, prefixlen = addr.split("/")
            ipr.addr("add", index=idx, address=ip_addr, prefixlen=int(prefixlen))

    _in_namespace(pid, _op)
    log.debug("Ensured dummy %s in ns(%d)", ifname, pid)


def enable_mpls_input(pid: int, ifname: str) -> None:
    """Enable MPLS input on an interface inside a namespace."""
    err = _write_sysctl_in_netns(pid, f"net.mpls.conf.{ifname}.input", "1")
    if err:
        log.warning("Failed to enable MPLS input for %s in ns(%d): %s", ifname, pid, err)
