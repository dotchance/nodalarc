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
import errno
import ipaddress
import logging
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from nodalarc.platform_config import get_platform_config
from nodalarc.runtime_naming import link_interface_mac
from nodalarc.substrate.queue_sizing import LONG_DELAY_TRANSMIT_LEAF_PACKETS, queue_limit_packets
from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError
from pyroute2.netlink.rtnl import TC_H_ROOT

from node_agent import netem_plugin
from node_agent.kernel_constants import (
    NETEM_HANDLE,
    NUD_PERMANENT,
    SHAPER_CLASS_HANDLE,
    SHAPER_DEFAULT_CLASS,
    SHAPER_ROOT_HANDLE,
)
from node_agent.tc_dump import interface_qdiscs
from node_agent.tc_units import (
    delay_ms_to_netem_ns,
    htb_class,
)

log = logging.getLogger(__name__)

# Netem requests below carry their delay as delay_ns, which only this plugin encodes.
netem_plugin.register()

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


def assign_link_mac(pid: int, ifname: str, node_id: str, ipr: IPRoute | None = None) -> None:
    """Give a pod's link interface its MAC address (``runtime_naming.link_interface_mac``).

    If ipr is provided, we are already inside _in_namespace and use it directly.
    """
    mac = link_interface_mac(node_id, ifname)

    def _do_assign(handle: IPRoute) -> None:
        idx = handle.link_lookup(ifname=ifname)
        if not idx:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        handle.link("set", index=idx[0], address=mac)

    if ipr is not None:
        _do_assign(ipr)
    else:
        _in_namespace(pid, _do_assign)


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
# The explicit leaf of a long-delay transmitter's rate class: its untransmitted backlog.
TRANSMIT_LEAF_HANDLE = 0x00140000  # 20:


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
    root = next((q for q in interface_qdiscs(idx) if q["parent"] == TC_H_ROOT), None)
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
    delay_ns = delay_ms_to_netem_ns(delay_ms)
    limit = queue_limit_packets(transmit_mbps=transmit_mbps, delay_ms=delay_ms)

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
            delay_ns=delay_ns,
            limit=limit,
        )

    _in_namespace(pid, _op)
    log.debug(
        "Applied transmit shaping on ns(%s)/%s: %sms, %sMbps", pid, ifname, delay_ms, transmit_mbps
    )


def apply_receive_shaping(host_ifname: str, receive_mbps: float, *, access: bool = False) -> None:
    """Shape what a pod interface receives: its terminal's receive rate.

    Every pod interface is fed through its host-side veth, so that veth's
    egress is the pod interface's ingress. An access interface (``access``) links to a peer per
    pass, and a long-delay pass leaves its receive delay queue under the rate class; a local pass
    carries its delay on the transmitting side, so that queue goes. The end carries no contact
    then: the long-delay pass closed, and its queue was counted at the close.
    """

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        if access:
            for qdisc in interface_qdiscs(links[0]):
                if qdisc["parent"] == _SHAPER_CLASS and qdisc.get_attr("TCA_KIND") == "netem":
                    ipr.tc("del", index=links[0], handle=qdisc["handle"], parent=_SHAPER_CLASS)
        _rate_limit_egress(ipr, links[0], receive_mbps)

    in_host_namespace(_op)
    log.debug("Applied receive shaping on host/%s: %sMbps", host_ifname, receive_mbps)


def update_delay(pid: int, ifname: str, delay_ms: float, transmit_mbps: float) -> None:
    """Change the netem delay, and the queue limit that follows it, on a shaped interface.

    Uses tc "change": the shaper from apply_transmit_shaping() must exist.
    Every change carries the limit, because pyroute2 sets a change that
    omits it to the kernel default of 1000 packets.
    """
    delay_ns = delay_ms_to_netem_ns(delay_ms)
    limit = queue_limit_packets(transmit_mbps=transmit_mbps, delay_ms=delay_ms)

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
            delay_ns=delay_ns,
            limit=limit,
        )

    _in_namespace(pid, _op)


# ---------------------------------------------------------------------------
# Long-delay link ends: the delay queue on the receiving side
# ---------------------------------------------------------------------------
#
# A long-delay direction's delay queue lives on the receiving end's host-side veth, whose egress
# is the receiving pod interface's ingress; the transmitting pod interface keeps only the
# terminal's rate. A packet that entered the queue is already on its way to that receiver, so a
# transmitter given a new peer never hands it the old peer's packets, and stopping admission at
# the transmitter leaves the queue to drain. Rejected alternative: the delay queue on the
# transmitting interface, as local links have it. Measured in scratch namespaces: with the queue
# there, all 200 packets queued for peer A were delivered to peer B after the transmitter was
# repointed; with one queue per contact on the receiving side, A received its 200 and B only its
# own.


def apply_long_delay_transmit_shaping(
    pid: int, ifname: str, transmit_mbps: float, *, access: bool = False
) -> None:
    """Shape a long-delay end's transmitting pod interface: the terminal's rate, no delay queue,
    and the rate class's queue as an explicit pfifo leaf (handle 20:) of
    ``LONG_DELAY_TRANSMIT_LEAF_PACKETS``, the limit the memory budget counts.

    The leaf is explicit so the stop can purge it: deleting a leaf drops what it holds and adds
    the count to the root qdisc's drops, which is how the stop counts the untransmitted backlog
    exactly (``admission.purge_untransmitted``). Rejected alternative: HTB's default leaf, which
    has no handle and cannot be deleted. Called with admission stopped, so the leaf it replaces
    holds nothing.

    A fixed link's class is fixed for the session, so a delay queue found on it is another link
    class's state, refused rather than removed. An access interface (``access``) links to a peer
    per pass, and a peer's class is that pass's: the delay queue a local pass left on it is
    replaced. The end carries no contact then (PREPARE checks it), so the queue holds only what
    the ended local pass still had, already lost at its LinkDown.
    """

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        netem = [q for q in interface_qdiscs(idx) if q.get_attr("TCA_KIND") == "netem"]
        if netem and not access:
            raise RuntimeError(
                f"{ifname} is a long-delay terminal interface and holds a delay queue; its "
                "delay belongs on the receiving side"
            )
        if netem:
            ipr.tc("del", index=idx, parent=TC_H_ROOT)
        _rate_limit_egress(ipr, idx, transmit_mbps)
        leaf = next((q for q in interface_qdiscs(idx) if q["parent"] == _SHAPER_CLASS), None)
        if leaf is not None:
            if leaf["handle"] == TRANSMIT_LEAF_HANDLE and leaf.get_attr("TCA_KIND") == "pfifo":
                return
            raise RuntimeError(
                f"the rate class of long-delay transmitter {ifname} holds a "
                f"{leaf.get_attr('TCA_KIND')} qdisc (handle {leaf['handle']:#x}), not its leaf"
            )
        ipr.tc(
            "add",
            kind="pfifo",
            index=idx,
            handle=TRANSMIT_LEAF_HANDLE,
            parent=_SHAPER_CLASS,
            limit=LONG_DELAY_TRANSMIT_LEAF_PACKETS,
        )

    _in_namespace(pid, _op)


def apply_receive_delay_queue(
    host_ifname: str, receive_mbps: float, delay_ms: float, limit_packets: int, *, fresh: bool
) -> None:
    """Give a long-delay end's host-side veth its receive queue: the terminal's receive rate and
    the delay of the direction the end receives, held in a queue of ``limit_packets``.

    ``fresh`` (the end carries no contact) starts a new queue: the end's last queue was emptied
    at its close, and a new qdisc's counters count this contact alone. On a host-side veth that
    lives for the session, keeping the old qdisc would add every earlier contact's drops to this
    one's. A contact that reopens while the last one drains keeps the queue and its packets."""
    delay_ns = delay_ms_to_netem_ns(delay_ms)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        idx = links[0]
        if fresh:
            root = next((q for q in interface_qdiscs(idx) if q["parent"] == TC_H_ROOT), None)
            if root is not None and root["handle"] != 0:
                ipr.tc("del", index=idx, parent=TC_H_ROOT)
        _rate_limit_egress(ipr, idx, receive_mbps)
        ipr.tc(
            "replace",
            kind="netem",
            index=idx,
            handle=_NETEM_HANDLE,
            parent=_SHAPER_CLASS,
            delay_ns=delay_ns,
            limit=limit_packets,
        )

    in_host_namespace(_op)


def update_receive_delay(host_ifname: str, delay_ms: float, limit_packets: int) -> None:
    """Change a long-delay end's receive delay, and the queue limit sized for it. Every change
    carries the limit, because pyroute2 sets a change that omits it to netem's default."""
    delay_ns = delay_ms_to_netem_ns(delay_ms)

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        ipr.tc(
            "change",
            kind="netem",
            index=links[0],
            handle=_NETEM_HANDLE,
            parent=_SHAPER_CLASS,
            delay_ns=delay_ns,
            limit=limit_packets,
        )

    in_host_namespace(_op)


def host_peer_of(pid: int, ifname: str) -> str:
    """The host-side veth paired with a pod link interface, read from the kernel: a veth pod end
    records its peer's index in the host namespace."""

    def _pod(ipr: IPRoute) -> int:
        links = ipr.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        (link,) = ipr.get_links(links[0])
        peer = link.get_attr("IFLA_LINK")
        if not peer:
            raise RuntimeError(f"{ifname} in ns({pid}) names no peer device")
        return int(peer)

    peer_index = _in_namespace(pid, _pod)

    def _host(ipr: IPRoute) -> str:
        (link,) = ipr.get_links(peer_index)
        return str(link.get_attr("IFLA_IFNAME"))

    return in_host_namespace(_host)


@dataclass(frozen=True, slots=True)
class ContactTag:
    """What a long-delay end's host-side veth records about the contact its queues belong to:
    the contact, and the counts already reported for it (packets its admission filter took,
    drops of its receive queue, drops of its transmit rate queue). Each report from the end is
    the counts above these.

    The kernel keeps it, in the device's alias. Rejected alternative: keeping the reported counts
    in the Scheduler's memory or this process's. Either restarts, and a contact that outlives the
    restart would report its earlier loss again."""

    contact_id: str
    # The end the contact links this one to, "node/interface": a ground terminal links to a peer
    # chosen per pass, and a restarted Scheduler finds each pass's two ends by it.
    peer: str
    taken_reported: int
    dropped_reported: int
    transmit_dropped_reported: int

    def encoded(self) -> str:
        return (
            f"{self.contact_id};peer={self.peer};taken={self.taken_reported};"
            f"dropped={self.dropped_reported};tx_dropped={self.transmit_dropped_reported}"
        )

    @classmethod
    def decoded(cls, alias: str) -> ContactTag:
        contact_id, *fields = alias.split(";")
        values = dict(field.partition("=")[::2] for field in fields)
        if not contact_id or set(values) != {"peer", "taken", "dropped", "tx_dropped"}:
            raise ValueError(f"a contact tag of an unknown form: {alias!r}")
        return cls(
            contact_id,
            values["peer"],
            int(values["taken"]),
            int(values["dropped"]),
            int(values["tx_dropped"]),
        )


def tag_contact(host_ifname: str, tag: ContactTag | None) -> None:
    """Record on a long-delay end's host-side veth the contact its receive queue belongs to (the
    interface alias), so a stage for an earlier contact can never change a later one; None
    clears it. The kernel keeps it: a restarted Node Agent reads the contact from the device."""

    def _op(ipr: IPRoute) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        ipr.link("set", index=links[0], ifalias="" if tag is None else tag.encoded())

    in_host_namespace(_op)


def contact_tag(host_ifname: str) -> ContactTag | None:
    """The contact a long-delay end's host-side veth carries; None when it carries none (its
    alias empty) or the device is gone."""

    def _op(ipr: IPRoute) -> ContactTag | None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            return None
        (link,) = ipr.get_links(links[0])
        alias = link.get_attr("IFLA_IFALIAS")
        return ContactTag.decoded(alias) if alias else None

    return in_host_namespace(_op)


def contact_of(host_ifname: str) -> str | None:
    """The contact id a long-delay end's host-side veth carries, None when it carries none."""
    tag = contact_tag(host_ifname)
    return tag.contact_id if tag is not None else None


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
    mac = link_interface_mac(node_id, ifname)

    def _do_configure(handle: IPRoute) -> None:
        assign_link_mac(pid, ifname, node_id, ipr=handle)
        # Disable IPv6 autoconfig — already in namespace, pass handle
        disable_ipv6_autoconfig(pid, ifname, ipr=handle)

    if ipr is not None:
        _do_configure(ipr)
    else:
        _in_namespace(pid, _do_configure)
    log.debug("Configured %s in ns(%d): mac=%s, ipv6_autoconfig=off", ifname, pid, mac)


def set_pod_interface_down(pid: int, ifname: str) -> None:
    """Bring a pod interface admin down: a satellite's terminal deactivated at the close of its
    long-delay ground pass, as at a local ground LinkDown."""

    def _do(handle: IPRoute) -> None:
        idx = handle.link_lookup(ifname=ifname)
        if not idx:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        handle.link("set", index=idx[0], state="down")

    _in_namespace(pid, _do)


def install_static_neighbors(
    pid: int, ifname: str, lladdr: str, addresses: tuple[str, ...]
) -> None:
    """Pin each peer address to the peer's link address on a pod interface.

    On a long-delay link address resolution cannot complete: the kernel gives up on an ARP or
    neighbor-discovery request after about 3 s, and the reply needs twice the light time. A
    permanent entry is what a point-to-point link without resolution has. ``replace`` makes the
    write idempotent across repeated LinkUps.
    """

    def _do(handle: IPRoute) -> None:
        idx = handle.link_lookup(ifname=ifname)
        if not idx:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        for address in addresses:
            family = (
                socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
            )
            handle.neigh(
                "replace",
                dst=address,
                lladdr=lladdr,
                ifindex=idx[0],
                state=NUD_PERMANENT,
                family=family,
            )

    _in_namespace(pid, _do)


def remove_static_neighbors(pid: int, ifname: str, addresses: tuple[str, ...]) -> None:
    """Remove the pins ``install_static_neighbors`` made on a pod interface. An access interface
    is linked to another peer on its next pass, so the last peer's pins go at its LinkDown. An
    address with no entry is already removed (a repeated LinkDown)."""

    def _do(handle: IPRoute) -> None:
        idx = handle.link_lookup(ifname=ifname)
        if not idx:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        for address in addresses:
            family = (
                socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
            )
            try:
                handle.neigh("del", dst=address, ifindex=idx[0], family=family)
            except NetlinkError as exc:
                if exc.code != errno.ENOENT:
                    raise

    _in_namespace(pid, _do)
