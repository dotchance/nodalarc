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
Node Agent enforces it in two layers, both inside the pod's network
namespace and both below any routing software or router configuration:

- Ethernet layer (tc ``clsact`` filters on cni0). Inbound, only TCP to port
  22 (IPv4 or IPv6) passes, plus neighbor resolution. Outbound, only TCP
  from port 22 passes, plus neighbor resolution. Neighbor resolution (ARP,
  and IPv6 neighbor solicitations and advertisements) passes both ways
  because answering an inbound connection needs it: the pod must resolve its
  pod network gateway's link-layer address to send a reply, and keep that
  entry valid. Every other frame is dropped in both directions: IS-IS and
  any other non-IP frame, and all other IPv4 and IPv6. A router
  configuration that runs a protocol on cni0 cannot put a frame on it.
- Connection tracking (a NodalArc nftables table, IPv4 and IPv6). Outbound
  IP traffic on cni0 leaves only for connections already established, or as
  IPv6 neighbor discovery, so TCP from port 22 is only ever a reply.

cni0 also sits in its own VRF (``nodalarc-mgmt``, wiring.py), so its routes
are never part of the emulation's routing table.
"""

from __future__ import annotations

import errno
import struct

from pyroute2.netlink.exceptions import NetlinkError
from pyroute2.nftables.expressions import genex, verdict
from pyroute2.nftables.main import NFTables

CNI_INTERFACE = "cni0"
POD_TABLE = "nodalarc"

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
_IPV6_NEXT_TCP = "0x00000600/0x0000ff00+4"
_IPV6_NEXT_ICMPV6 = "0x00003a00/0x0000ff00+4"
_TCP6_DPORT_SSH = "0x00000016/0x0000ffff+40"
_TCP6_SPORT_SSH = "0x00160000/0xffff0000+40"
_ND_SOLICITATION = "0x87000000/0xff000000+40"
_ND_ADVERTISEMENT = "0x88000000/0xff000000+40"
_MATCH_ALL = "0x0/0x0+0"

# (direction parent, prio, protocol, keys, action) — first match wins by prio.
FILTERS = tuple(
    entry
    for parent, ssh_port in (
        (_CLSACT_INGRESS, (_TCP_DPORT_SSH, _TCP6_DPORT_SSH)),
        (_CLSACT_EGRESS, (_TCP_SPORT_SSH, _TCP6_SPORT_SSH)),
    )
    for entry in (
        (parent, 1, _ETH_P_ARP, (_MATCH_ALL,), "ok"),
        (parent, 2, _ETH_P_IP, (_IPV4_NO_OPTIONS, _IPV4_TCP, ssh_port[0]), "ok"),
        (parent, 3, _ETH_P_IPV6, (_IPV6_NEXT_TCP, ssh_port[1]), "ok"),
        (parent, 4, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_SOLICITATION), "ok"),
        (parent, 5, _ETH_P_IPV6, (_IPV6_NEXT_ICMPV6, _ND_ADVERTISEMENT), "ok"),
        (parent, 6, _ETH_P_ALL, (_MATCH_ALL,), "drop"),
    )
)

_NFPROTO_INET = 1
_NFT_META_OIFNAME = 7
_NFT_META_L4PROTO = 16
_NFT_PAYLOAD_TRANSPORT_HEADER = 2
_IPPROTO_ICMPV6 = 58
_ND_NEIGHBOR_SOLICITATION, _ND_NEIGHBOR_ADVERTISEMENT = 135, 136
_NFT_CT_STATE = 0
_NFT_CMP_EQ, _NFT_CMP_NEQ = 0, 1
_NF_DROP, _NF_ACCEPT = 0, 1
_CT_ESTABLISHED = 2


def _data(value: bytes) -> dict:
    return {"attrs": [("NFTA_DATA_VALUE", value)]}


def _conntrack_rule() -> None:
    """Replace the pod's NodalArc nftables table: cni0 carries only established traffic out.

    One inet table covers IPv4 and IPv6. IPv6 neighbor solicitations and
    advertisements are not tracked connections, so they are accepted by type.
    """
    oif_is_cni0 = [
        genex("meta", {"key": _NFT_META_OIFNAME, "dreg": 1}),
        genex(
            "cmp",
            {"sreg": 1, "op": _NFT_CMP_EQ, "data": _data(CNI_INTERFACE.encode().ljust(16, b"\0"))},
        ),
    ]
    established = [
        genex("ct", {"key": _NFT_CT_STATE, "dreg": 1}),
        genex(
            "bitwise",
            {
                "sreg": 1,
                "dreg": 1,
                "len": 4,
                "mask": _data(struct.pack("=I", _CT_ESTABLISHED)),
                "xor": _data(b"\0" * 4),
            },
        ),
        genex("cmp", {"sreg": 1, "op": _NFT_CMP_NEQ, "data": _data(b"\0" * 4)}),
    ]

    def neighbor_discovery(icmpv6_type: int) -> list:
        return [
            genex("meta", {"key": _NFT_META_L4PROTO, "dreg": 1}),
            genex("cmp", {"sreg": 1, "op": _NFT_CMP_EQ, "data": _data(bytes([_IPPROTO_ICMPV6]))}),
            genex(
                "payload",
                {"dreg": 1, "base": _NFT_PAYLOAD_TRANSPORT_HEADER, "offset": 0, "len": 1},
            ),
            genex("cmp", {"sreg": 1, "op": _NFT_CMP_EQ, "data": _data(bytes([icmpv6_type]))}),
        ]

    with NFTables(nfgen_family=_NFPROTO_INET) as nft:
        try:
            nft.table("del", name=POD_TABLE)
        except NetlinkError as exc:
            if exc.code != errno.ENOENT:
                raise
        nft.table("add", name=POD_TABLE)
        nft.chain(
            "add", table=POD_TABLE, name="output", hook="output", type="filter", policy=_NF_ACCEPT
        )
        nft.rule(
            "add",
            table=POD_TABLE,
            chain="output",
            expressions=(oif_is_cni0, established, verdict(code=_NF_ACCEPT)),
        )
        for icmpv6_type in (_ND_NEIGHBOR_SOLICITATION, _ND_NEIGHBOR_ADVERTISEMENT):
            nft.rule(
                "add",
                table=POD_TABLE,
                chain="output",
                expressions=(
                    oif_is_cni0,
                    neighbor_discovery(icmpv6_type),
                    verdict(code=_NF_ACCEPT),
                ),
            )
        nft.rule(
            "add",
            table=POD_TABLE,
            chain="output",
            expressions=(oif_is_cni0, verdict(code=_NF_DROP)),
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

    Idempotent: every layer is replaced, never added to.
    """
    links = ipr.link_lookup(ifname=CNI_INTERFACE)
    if not links:
        raise RuntimeError(f"{CNI_INTERFACE} is missing")
    _conntrack_rule()
    _frame_filters(ipr, links[0])
