# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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

from nodalarc.platform_config import get_platform_config
from pyroute2 import IPRoute
from pyroute2.netlink.rtnl import TC_H_ROOT

from node_agent.kernel_constants import (
    NETEM_HANDLE,
    SHAPER_CLASS_HANDLE,
    SHAPER_DEFAULT_CLASS,
    SHAPER_ROOT_HANDLE,
)
from node_agent.tc_units import (
    delay_ms_to_netem_us,
    htb_class,
    netem_limit_packets,
)

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


def in_host_namespace(fn: Callable[[IPRoute], _T]) -> _T:
    """Execute fn(ipr) in the host network namespace, serialized with _ns_lock."""
    with _ns_lock:
        ret = _libc.setns(_get_host_ns_fd(), _CLONE_NEWNET)
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")
        ipr = IPRoute()
        try:
            return fn(ipr)
        finally:
            ipr.close()


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


# Egress shaping hierarchy on one interface: an HTB root whose one class
# carries the terminal's rate (HTB carries 64-bit rates, so terminals above
# 34 Gbit/s are shaped at their declared rate), with a netem child for the
# one-way delay on the transmitting side.
_SHAPER_ROOT = SHAPER_ROOT_HANDLE  # 1:
_SHAPER_CLASS = SHAPER_CLASS_HANDLE  # 1:1
_NETEM_HANDLE = NETEM_HANDLE  # 10:


def _is_shaper_root(qdisc) -> bool:
    """True for the HTB root at 1: that sends all traffic to class 1:1."""
    if qdisc["handle"] != _SHAPER_ROOT or qdisc.get_attr("TCA_KIND") != "htb":
        return False
    init = qdisc.get_attr("TCA_OPTIONS").get_attr("TCA_HTB_INIT")
    return init is not None and init["defcls"] == SHAPER_DEFAULT_CLASS


def _install_shaper_root(ipr: IPRoute, idx: int) -> None:
    """Make the interface's root qdisc the HTB shaper root.

    The kernel cannot change an HTB root in place, so an existing shaper root
    stays as it is. Any other root the interface carries (the former tbf
    shaper, or an HTB root with another default class) is deleted first; the
    device's default qdisc (handle 0) is replaced by the add itself.
    """
    root = next((q for q in ipr.get_qdiscs(index=idx) if q["parent"] == TC_H_ROOT), None)
    if root is not None and _is_shaper_root(root):
        return
    if root is not None and root["handle"] != 0:
        log.info(
            "Replacing root qdisc %s (handle %#x) on ifindex %s with the HTB shaper",
            root.get_attr("TCA_KIND"),
            root["handle"],
            idx,
        )
        ipr.tc("del", index=idx, parent=TC_H_ROOT)
    ipr.tc("add", kind="htb", index=idx, handle=_SHAPER_ROOT, default=SHAPER_DEFAULT_CLASS)


def _rate_limit_egress(ipr: IPRoute, idx: int, rate_mbps: float) -> None:
    """Shape an interface's egress to ``rate_mbps`` through HTB class 1:1."""
    shaper = htb_class(rate_mbps, get_platform_config().veth_interface_mtu_bytes)
    _install_shaper_root(ipr, idx)
    ipr.tc(
        "replace-class",
        kind="htb",
        index=idx,
        handle=_SHAPER_CLASS,
        parent=_SHAPER_ROOT,
        rate=shaper.rate,
        ceil=shaper.ceil,
        burst=shaper.burst,
        cburst=shaper.cburst,
        quantum=shaper.quantum,
    )


def apply_transmit_shaping(pid: int, ifname: str, delay_ms: float, transmit_mbps: float) -> None:
    """Shape a pod interface's egress: the terminal's transmit rate and the link delay.

    Strictly idempotent through replace semantics: safe on a fresh, a
    previously shaped or an orphaned interface. Called on LinkUp; later
    delay-only changes use update_delay().
    """
    delay_us = delay_ms_to_netem_us(delay_ms)
    limit = netem_limit_packets(transmit_mbps, delay_ms)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        _rate_limit_egress(ipr, idx, transmit_mbps)
        ipr.tc(
            "replace",
            kind="netem",
            index=idx,
            handle=_NETEM_HANDLE,
            parent=_SHAPER_CLASS,
            delay=delay_us,
            limit=limit,
        )

    _in_namespace(pid, _op)
    log.debug(
        "Applied transmit shaping on ns(%s)/%s: %sms, %sMbps", pid, ifname, delay_ms, transmit_mbps
    )


def apply_receive_shaping(host_ifname: str, receive_mbps: float) -> None:
    """Shape what a pod interface receives: its terminal's receive rate.

    Every pod interface is fed through its host-side veth, so that veth's
    egress is the pod interface's ingress.
    """

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        _rate_limit_egress(ipr, links[0], receive_mbps)

    in_host_namespace(_op)
    log.debug("Applied receive shaping on host/%s: %sMbps", host_ifname, receive_mbps)


def update_delay(pid: int, ifname: str, delay_ms: float, transmit_mbps: float) -> None:
    """Change the netem delay, and the queue limit that follows it, on a shaped interface.

    Uses tc "change": the shaper from apply_transmit_shaping() must exist.
    Every change carries the limit, because pyroute2 sets a change that
    omits it to the kernel default of 1000 packets.
    """
    delay_us = delay_ms_to_netem_us(delay_ms)
    limit = netem_limit_packets(transmit_mbps, delay_ms)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ipr.tc(
            "change",
            kind="netem",
            index=links[0],
            handle=_NETEM_HANDLE,
            parent=_SHAPER_CLASS,
            delay=delay_us,
            limit=limit,
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
