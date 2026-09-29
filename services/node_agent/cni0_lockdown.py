# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The cni0 lockdown in every session pod: SSH from outside in, nothing else.

``cni0`` is the session pod's Kubernetes interface (renamed at boot). The
emulated world has no such network, so nothing in the pod may use it: no
routing protocol, no application traffic, no neighbor discovery of its own,
in any protocol and either direction. The one exception is management:
SSH connections made to the pod from outside (the browser terminal through
VS-API), and the replies to them. Nothing inside the pod ever opens anything
on cni0.

The lockdown follows whichever address families the Kubernetes pod network
gave cni0 (IPv4, IPv6 or both) and changes none of cni0's addresses. The
Node Agent enforces it with tc ``clsact`` filters on cni0, inside the pod's
network namespace and below any routing software or router configuration.
Inbound, only TCP to port 22 (IPv4 or IPv6) passes, plus neighbor
resolution. Outbound, only TCP from port 22 passes, plus neighbor
resolution, and a segment from port 22 that opens a connection (SYN without
ACK) is dropped, so TCP from port 22 only ever answers a connection made
from outside. Neighbor resolution (ARP, and IPv6 neighbor solicitations and
advertisements) passes both ways because answering an inbound connection
needs it: the pod must resolve its pod network gateway's link-layer address
to send a reply, and keep that entry valid. Every other frame is dropped in
both directions: IS-IS and any other non-IP frame, and all other IPv4 and
IPv6. A router configuration that runs a protocol on cni0 cannot put a
frame on it.

The filters are stateless and act on cni0 alone. Nothing here touches the
emulated interfaces or changes how the pod forwards emulated traffic: a
connection-tracking rule would switch tracking and fragment reassembly on
for every packet the emulated router forwards.

cni0 also sits in its own VRF (``nodalarc-mgmt``, wiring.py), so its routes
are never part of the emulation's routing table. The FRR image's sshd
listens inside that VRF for terminals (``ListenAddress ... rdomain
nodalarc-mgmt``) and in the default VRF for SSH over the emulated network.
"""

from __future__ import annotations

import errno

from pyroute2.netlink.exceptions import NetlinkError

CNI_INTERFACE = "cni0"

_ETH_P_ALL, _ETH_P_IP, _ETH_P_ARP, _ETH_P_IPV6 = 0x0003, 0x0800, 0x0806, 0x86DD
_CLSACT_INGRESS, _CLSACT_EGRESS = 0xFFFFFFF2, 0xFFFFFFF3
# u32 keys, value/mask+offset from the start of the network header. IPv4 keys
# require a 20-byte header (no options) and IPv6 keys a TCP or ICMPv6 header
# right after the 40-byte fixed header (no extension headers): the terminal's
# SSH traffic and neighbor discovery carry neither, and anything else fails
# closed.
_IPV4_NO_OPTIONS = "0x05000000/0x0f000000+0"
_IPV4_TCP = "0x00060000/0x00ff0000+8"
_TCP_DPORT_SSH = "0x00000016/0x0000ffff+20"
_TCP_SPORT_SSH = "0x00160000/0xffff0000+20"
# TCP flags are the second byte of the header's fourth word: SYN set, ACK clear.
_TCP_SYN_WITHOUT_ACK = "0x00020000/0x00120000+32"
_IPV6_NEXT_TCP = "0x00000600/0x0000ff00+4"
_IPV6_NEXT_ICMPV6 = "0x00003a00/0x0000ff00+4"
_TCP6_DPORT_SSH = "0x00000016/0x0000ffff+40"
_TCP6_SPORT_SSH = "0x00160000/0xffff0000+40"
_TCP6_SYN_WITHOUT_ACK = "0x00020000/0x00120000+52"
_ND_SOLICITATION = "0x87000000/0xff000000+40"
_ND_ADVERTISEMENT = "0x88000000/0xff000000+40"
_MATCH_ALL = "0x0/0x0+0"

_IPV4_SSH_IN = (_IPV4_NO_OPTIONS, _IPV4_TCP, _TCP_DPORT_SSH)
_IPV4_SSH_OUT = (_IPV4_NO_OPTIONS, _IPV4_TCP, _TCP_SPORT_SSH)
_IPV6_SSH_IN = (_IPV6_NEXT_TCP, _TCP6_DPORT_SSH)
_IPV6_SSH_OUT = (_IPV6_NEXT_TCP, _TCP6_SPORT_SSH)

# (direction parent, prio, protocol, keys, action) — first match wins by prio.
FILTERS = (
    (_CLSACT_INGRESS, 1, _ETH_P_ARP, (_MATCH_ALL,), "ok"),
    (_CLSACT_INGRESS, 2, _ETH_P_IP, _IPV4_SSH_IN, "ok"),
    (_CLSACT_INGRESS, 3, _ETH_P_IPV6, _IPV6_SSH_IN, "ok"),
    (_CLSACT_INGRESS, 4, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_SOLICITATION), "ok"),
    (_CLSACT_INGRESS, 5, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_ADVERTISEMENT), "ok"),
    (_CLSACT_INGRESS, 9, _ETH_P_ALL, (_MATCH_ALL,), "drop"),
    (_CLSACT_EGRESS, 1, _ETH_P_ARP, (_MATCH_ALL,), "ok"),
    (_CLSACT_EGRESS, 2, _ETH_P_IP, (*_IPV4_SSH_OUT, _TCP_SYN_WITHOUT_ACK), "drop"),
    (_CLSACT_EGRESS, 3, _ETH_P_IP, _IPV4_SSH_OUT, "ok"),
    (_CLSACT_EGRESS, 4, _ETH_P_IPV6, (*_IPV6_SSH_OUT, _TCP6_SYN_WITHOUT_ACK), "drop"),
    (_CLSACT_EGRESS, 5, _ETH_P_IPV6, _IPV6_SSH_OUT, "ok"),
    (_CLSACT_EGRESS, 6, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_SOLICITATION), "ok"),
    (_CLSACT_EGRESS, 7, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_ADVERTISEMENT), "ok"),
    (_CLSACT_EGRESS, 9, _ETH_P_ALL, (_MATCH_ALL,), "drop"),
)


def _frame_filters(ipr, index: int) -> None:
    """Replace cni0's clsact filters with FILTERS, then prove every one is installed."""
    try:
        ipr.tc("del", "clsact", index)
    except NetlinkError as exc:
        if exc.code not in (errno.ENOENT, errno.EINVAL):
            raise
    ipr.tc("add", "clsact", index)
    for parent, prio, protocol, keys, action in FILTERS:
        ipr.tc(
            "add-filter",
            "u32",
            index,
            parent=parent,
            prio=prio,
            protocol=protocol,
            target=0,
            keys=list(keys),
            action=action,
        )
    for parent in (_CLSACT_INGRESS, _CLSACT_EGRESS):
        installed = {
            message["info"] >> 16
            for message in ipr.get_filters(index=index, parent=parent)
            if message.get_attr("TCA_KIND") == "u32" and message.get_attr("TCA_OPTIONS")
        }
        expected = {prio for filter_parent, prio, *_ in FILTERS if filter_parent == parent}
        if not expected <= installed:
            raise RuntimeError(
                f"cni0 filters did not read back: parent {parent:#x} has priorities "
                f"{sorted(installed)}, expected {sorted(expected)}"
            )


def lock_down_cni0_here(ipr) -> None:
    """Apply the cni0 lockdown in the calling thread's network namespace (a session pod's).

    Idempotent: the filters are replaced, never added to.
    """
    links = ipr.link_lookup(ifname=CNI_INTERFACE)
    if not links:
        raise RuntimeError(f"{CNI_INTERFACE} is missing")
    _frame_filters(ipr, links[0])
