# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The network namespace that holds every emulated LAN bridge on a server.

An emulated LAN is an emulated site LAN or a satellite bus. On each server,
the Node Agent carries each emulated LAN with one Linux bridge (``sl…``), a
veth pair per member (the bridge-port end ``sm…`` on the bridge, the
member's interface in its session pod) and, when members are on other
servers, a VXLAN port (``sv…``). All of those live in one namespace per
server, ``emulated_lan``, never in the server namespace.

The reason is Kubernetes' required ``bridge-nf-call-iptables=1``: in the
server namespace it sends every IP frame crossing any Linux bridge through
the server's firewall and NAT. Frames between emulated nodes on an emulated
LAN then met Docker's FORWARD DROP (IPv4 dropped while ARP and IS-IS still
worked), kube-proxy and kube-router rules that act on the cluster's pod and
service address ranges, and the server's connection tracking. The setting is
per namespace; in ``emulated_lan``:

- bridge netfilter is off (``bridge-nf-call-*`` = 0), so a frame crossing an
  emulated LAN bridge never reaches a firewall;
- forwarding is off for IPv4 and IPv6, and IPv6 is off on every device, so
  no device carries an address: nothing in the namespace routes between
  emulated LANs, and no bridge or port sends frames of its own (IPv6
  link-local solicitations, MLD) onto an emulated LAN. A frame reaches only
  the other ports of its own bridge. A veth end moved into a session pod
  takes the pod namespace's IPv6 settings, so the member's interface keeps
  IPv6.

The server's other bridges (flannel's cni0 for the pod network, docker0) are
not NodalArc's and stay in the server namespace.

The namespace is pinned by a bind mount at ``/run/netns/emulated_lan``
(where ``ip netns`` looks), so it outlives Node Agent restarts. It is created
in a thread of its own, with ``unshare(CLONE_NEWNET)``, in the Node Agent's
process. The host cleaner removes it, which destroys every device in it.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import threading
from collections.abc import Callable
from typing import TypeVar

from pyroute2 import IPRoute

from node_agent.namespace_ops import _CLONE_NEWNET, _get_host_ns_fd, _libc, _ns_lock

log = logging.getLogger(__name__)

EMULATED_LAN_NAMESPACE = "emulated_lan"
EMULATED_LAN_NAMESPACE_PATH = f"/run/netns/{EMULATED_LAN_NAMESPACE}"
_MS_BIND = 4096
_MNT_DETACH = 2
_T = TypeVar("_T")

# Written inside the namespace when it is created.
_SETTINGS = {
    "net/ipv4/ip_forward": "0",
    "net/ipv4/conf/all/forwarding": "0",
    "net/ipv6/conf/all/forwarding": "0",
    "net/ipv6/conf/all/disable_ipv6": "1",
    "net/ipv6/conf/default/disable_ipv6": "1",
    "net/bridge/bridge-nf-call-iptables": "0",
    "net/bridge/bridge-nf-call-ip6tables": "0",
    "net/bridge/bridge-nf-call-arptables": "0",
}
# The bridge settings exist only while br_netfilter is loaded; without it no
# bridged frame reaches netfilter, so their absence proves the same thing.
_OPTIONAL = {key for key in _SETTINGS if key.startswith("net/bridge/")}


def _check(ret: int, what: str) -> None:
    if ret != 0:
        code = ctypes.get_errno()
        raise OSError(code, f"{what}: {os.strerror(code)}")


def _is_pinned() -> bool:
    """Whether the pin path holds a network namespace (a bind mount of nsfs)."""
    try:
        pinned = os.stat(EMULATED_LAN_NAMESPACE_PATH)
    except FileNotFoundError:
        return False
    return pinned.st_dev == os.stat("/proc/self/ns/net").st_dev


def _create() -> None:
    failure: list[BaseException] = []

    def _in_new_namespace() -> None:
        try:
            _check(_libc.unshare(_CLONE_NEWNET), "unshare(CLONE_NEWNET)")
            _check(
                _libc.mount(
                    b"/proc/thread-self/ns/net",
                    EMULATED_LAN_NAMESPACE_PATH.encode(),
                    None,
                    _MS_BIND,
                    None,
                ),
                f"bind mount of the new namespace at {EMULATED_LAN_NAMESPACE_PATH}",
            )
            for key, value in _SETTINGS.items():
                try:
                    with open(f"/proc/sys/{key}", "w", encoding="ascii") as handle:
                        handle.write(value)
                except FileNotFoundError:
                    if key not in _OPTIONAL:
                        raise
            with IPRoute() as ipr:
                ipr.link("set", index=ipr.link_lookup(ifname="lo")[0], state="up")
        except BaseException as exc:
            failure.append(exc)

    os.makedirs(os.path.dirname(EMULATED_LAN_NAMESPACE_PATH), exist_ok=True)
    os.close(os.open(EMULATED_LAN_NAMESPACE_PATH, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o444))
    # The thread's network namespace dies with it; the bind mount keeps it.
    thread = threading.Thread(target=_in_new_namespace, name="emulated-lan-namespace-create")
    thread.start()
    thread.join()
    if failure:
        remove_emulated_lan_namespace()
        raise failure[0]
    log.info("Created network namespace %s for emulated LAN bridges", EMULATED_LAN_NAMESPACE)


def ensure_emulated_lan_namespace() -> None:
    """Create the emulated LAN namespace unless it is already pinned."""
    if _is_pinned():
        return
    if os.path.exists(EMULATED_LAN_NAMESPACE_PATH):
        os.unlink(EMULATED_LAN_NAMESPACE_PATH)  # an empty pin file left without its mount
    _create()


def remove_emulated_lan_namespace() -> bool:
    """Unpin and delete the emulated LAN namespace; True when there was one to remove.

    The kernel destroys the namespace once nothing holds it, and every device
    in it goes with it (the peers of its veths in session pods included).
    """
    if not os.path.exists(EMULATED_LAN_NAMESPACE_PATH):
        return False
    if _is_pinned():
        ret = _libc.umount2(EMULATED_LAN_NAMESPACE_PATH.encode(), _MNT_DETACH)
        if ret != 0 and ctypes.get_errno() != errno.EINVAL:  # EINVAL: not a mount point
            _check(ret, f"unmount {EMULATED_LAN_NAMESPACE_PATH}")
    os.unlink(EMULATED_LAN_NAMESPACE_PATH)
    return True


def emulated_lan_namespace_present() -> bool:
    return os.path.exists(EMULATED_LAN_NAMESPACE_PATH)


def in_emulated_lan_namespace(fn: Callable[[IPRoute], _T]) -> _T:
    """Run ``fn(ipr)`` inside the emulated LAN namespace, then return to the server's.

    Serialized with every other namespace switch in the Node Agent.
    """
    lan_fd = os.open(EMULATED_LAN_NAMESPACE_PATH, os.O_RDONLY)
    try:
        with _ns_lock:
            _check(_libc.setns(lan_fd, _CLONE_NEWNET), f"setns to {EMULATED_LAN_NAMESPACE}")
            try:
                ipr = IPRoute()
                try:
                    return fn(ipr)
                finally:
                    ipr.close()
            finally:
                ret = _libc.setns(_get_host_ns_fd(), _CLONE_NEWNET)
                if ret != 0:
                    log.error("setns back to host failed: %s", os.strerror(ctypes.get_errno()))
    finally:
        os.close(lan_fd)


def open_emulated_lan_namespace() -> int:
    """An open descriptor of the emulated LAN namespace, for moving devices into it."""
    return os.open(EMULATED_LAN_NAMESPACE_PATH, os.O_RDONLY)
