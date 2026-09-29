# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Firewall rules the Node Agent keeps, made through netlink nftables in its own process.

Two rule sets:

- In each session pod, a NodalArc table drops new IPv4 egress on ``cni0``
  (the renamed Kubernetes interface), so the pod network can never carry
  emulated traffic; replies to connections made to the pod still leave.
- On the host, one rule at the top of iptables' own ``filter FORWARD`` chain,
  per address family, accepts forwarded traffic whose input and output
  devices are both site-LAN bridges. br_netfilter runs bridged frames
  through that chain with the bridge as both devices, so a frame crossing a
  site-LAN bridge matches and traffic leaving for any host or cluster
  interface never does. Without the rule, a host policy such as Docker's
  FORWARD DROP would discard site-LAN transit between session pods. The rule
  has to live in that chain: an accept in another table does not get a
  packet past a drop in this one.

  That chain belongs to iptables-nft, and other software on the host (K3s's
  network policy controller, kube-proxy) reads it with the iptables binary.
  iptables-nft refuses a chain holding an expression it cannot translate,
  and K3s exits when that read fails. So the rule uses only what an
  iptables rule would write: interface name prefixes and a comment,
  ``-i sl+ -o sl+ -m comment --comment nodalarc-site-lan-transit -j ACCEPT``.
  K3s's network policy controller saves and restores the whole filter table,
  which rewrites the rule in iptables-nft's own encoding (a counter and a
  comment match). The rule is therefore recognized by what it does, in
  either encoding, never by the bytes the Node Agent wrote.

The host rule is written into the nf_tables backend. A host whose iptables
uses the legacy backend keeps its FORWARD chain where netlink nftables
cannot reach it, so the Node Agent refuses to wire site LANs there.
"""

from __future__ import annotations

import errno
import logging
import struct
from pathlib import Path

from nodalarc.runtime_naming import SITE_LAN_BRIDGE_PREFIX
from pyroute2.netlink import NLM_F_DUMP, NLM_F_REQUEST
from pyroute2.netlink.exceptions import NetlinkError
from pyroute2.netlink.nfnetlink import NFNL_SUBSYS_NFTABLES
from pyroute2.netlink.nfnetlink.nftsocket import NFT_MSG_GETRULE, nft_rule_msg
from pyroute2.nftables.expressions import genex, verdict
from pyroute2.nftables.main import NFTables

log = logging.getLogger(__name__)

_NFPROTO = {4: 2, 6: 10}
_NFT_META_OIFNAME = 7
_NFT_META_IIFNAME = 6
_NFT_CT_STATE = 0
_NFT_CMP_EQ, _NFT_CMP_NEQ = 0, 1
_NF_DROP, _NF_ACCEPT = 0, 1
_CT_ESTABLISHED, _CT_RELATED = 2, 4

POD_TABLE = "nodalarc"
SITE_LAN_TRANSIT_COMMENT = "nodalarc-site-lan-transit"
_LEGACY_TABLE_LISTS = {4: "ip_tables_names", 6: "ip6_tables_names"}


def _data(value: bytes) -> dict:
    return {"attrs": [("NFTA_DATA_VALUE", value)]}


def _oif_is_cni0() -> list:
    return [
        genex("meta", {"key": _NFT_META_OIFNAME, "dreg": 1}),
        genex("cmp", {"sreg": 1, "op": _NFT_CMP_EQ, "data": _data(b"cni0".ljust(16, b"\0"))}),
    ]


def _established_or_related() -> list:
    return [
        genex("ct", {"key": _NFT_CT_STATE, "dreg": 1}),
        genex(
            "bitwise",
            {
                "sreg": 1,
                "dreg": 1,
                "len": 4,
                "mask": _data(struct.pack("=I", _CT_ESTABLISHED | _CT_RELATED)),
                "xor": _data(b"\0" * 4),
            },
        ),
        genex("cmp", {"sreg": 1, "op": _NFT_CMP_NEQ, "data": _data(b"\0" * 4)}),
    ]


def _between_site_lan_bridges() -> list:
    # A comparison shorter than the name compares a prefix: iptables' "sl+".
    prefix = _data(SITE_LAN_BRIDGE_PREFIX.encode())
    return [
        genex("meta", {"key": _NFT_META_IIFNAME, "dreg": 1}),
        genex("cmp", {"sreg": 1, "op": _NFT_CMP_EQ, "data": prefix}),
        genex("meta", {"key": _NFT_META_OIFNAME, "dreg": 1}),
        genex("cmp", {"sreg": 1, "op": _NFT_CMP_EQ, "data": prefix}),
    ]


def lock_down_cni0_here() -> None:
    """Replace this network namespace's NodalArc table with the cni0 egress lockdown.

    Runs in the calling thread's network namespace; the caller enters the
    pod's namespace first. The table is deleted and made again, so a second
    call leaves the same two rules.
    """
    with NFTables(nfgen_family=_NFPROTO[4]) as nft:
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
            expressions=(_oif_is_cni0(), _established_or_related(), verdict(code=_NF_ACCEPT)),
        )
        nft.rule(
            "add",
            table=POD_TABLE,
            chain="output",
            expressions=(_oif_is_cni0(), verdict(code=_NF_DROP)),
        )


def _refuse_legacy_backend(proc_net: Path) -> None:
    for version, listing in _LEGACY_TABLE_LISTS.items():
        try:
            tables = (proc_net / listing).read_text().split()
        except FileNotFoundError:
            continue  # the legacy module is not loaded: no legacy table exists
        if "filter" in tables:
            raise RuntimeError(
                f"the host's IPv{version} iptables filter table uses the legacy backend "
                f"({proc_net / listing} lists it); NodalArc keeps its FORWARD rule through "
                "nf_tables and cannot reach a legacy chain"
            )


def _forward_chain_rules(nft: NFTables, family: int) -> list | None:
    """Every rule of this family's ``filter FORWARD`` chain, or None when the chain is absent.

    The dump names the table and the chain, so the kernel returns that chain
    whole. pyroute2 0.9's own calls cannot make this request: its rule "get"
    fails, and its family-wide dump returned 1321 of 1469 rules on a K3s host.
    """
    request = nft_rule_msg()
    request["nfgen_family"] = family
    request["attrs"] = [["NFTA_RULE_TABLE", "filter"], ["NFTA_RULE_CHAIN", "FORWARD"]]
    core = nft.asyncore

    async def collect() -> list:
        responses = await core.nlm_request(
            request,
            msg_type=(NFNL_SUBSYS_NFTABLES << 8) | NFT_MSG_GETRULE,
            msg_flags=NLM_F_REQUEST | NLM_F_DUMP,
        )
        return [message async for message in responses]

    try:
        return core.event_loop.run_until_complete(collect())
    except NetlinkError as exc:
        if exc.code == errno.ENOENT:  # no filter table or no FORWARD chain in this family
            return None
        raise


def _is_transit_rule(message) -> bool:
    """Whether a rule accepts exactly what the transit rule accepts, in either encoding."""
    prefix = SITE_LAN_BRIDGE_PREFIX.encode()
    matched: set[str] = set()
    accepts = False
    key = None
    for expression in message.get_attr("NFTA_RULE_EXPRESSIONS") or ():
        name = expression.get_attr("NFTA_EXPR_NAME")
        data = expression.get_attr("NFTA_EXPR_DATA")
        if name == "meta":
            key = data.get_attr("NFTA_META_KEY")
        elif name == "cmp":
            value = data.get_attr("NFTA_CMP_DATA").get_attr("NFTA_DATA_VALUE")
            if (
                key not in ("NFT_META_IIFNAME", "NFT_META_OIFNAME")
                or data.get_attr("NFTA_CMP_OP") != "NFT_CMP_EQ"
                or value != prefix
            ):
                return False
            matched.add(key)
            key = None
        elif name == "immediate":
            code = (
                data.get_attr("NFTA_IMMEDIATE_DATA")
                .get_attr("NFTA_DATA_VERDICT")
                .get_attr("NFTA_VERDICT_CODE")
            )
            accepts = code == "NF_ACCEPT"
        elif name == "counter" or name == "match" and data.get_attr("NFTA_MATCH_NAME") == "comment":
            continue
        else:
            return False
    return accepts and matched == {"NFT_META_IIFNAME", "NFT_META_OIFNAME"}


def _transit_rule_handles(nft: NFTables, family: int) -> list[int] | None:
    rules = _forward_chain_rules(nft, family)
    if rules is None:
        return None
    return [rule.get_attr("NFTA_RULE_HANDLE") for rule in rules if _is_transit_rule(rule)]


def ensure_site_lan_transit_here(*, proc_net: Path = Path("/proc/net")) -> list[int]:
    """Keep exactly one site-LAN transit rule in this namespace's ``filter FORWARD`` chains.

    Runs in the calling thread's network namespace; the caller enters the
    host's. Returns the IP versions that carry the rule. A family without a
    ``filter FORWARD`` chain has no iptables policy to pass and gets none.
    Copies beyond the first are deleted. Raises when the legacy backend holds
    a filter table, or when the chain does not hold exactly one rule after.
    """
    _refuse_legacy_backend(proc_net)
    pinned = []
    for version, family in _NFPROTO.items():
        with NFTables(nfgen_family=family) as nft:
            handles = _transit_rule_handles(nft, family)
            if handles is None:
                continue
            if not handles:
                nft.rule(
                    "insert",
                    table="filter",
                    chain="FORWARD",
                    expressions=(_between_site_lan_bridges(), verdict(code=_NF_ACCEPT)),
                    userdata=SITE_LAN_TRANSIT_COMMENT,
                )
            for handle in handles[1:]:
                nft.rule("del", table="filter", chain="FORWARD", handle=handle)
            after = _transit_rule_handles(nft, family) or []
            if len(after) != 1:
                raise RuntimeError(
                    f"the host's IPv{version} filter FORWARD chain holds {len(after)} site-LAN "
                    "transit rules; exactly one was to remain"
                )
            pinned.append(version)
    return pinned


def remove_site_lan_transit_here() -> int:
    """Delete every site-LAN transit rule from this namespace's ``filter FORWARD`` chains.

    Returns how many were deleted. A rule already gone is absence.
    """
    removed = 0
    for family in _NFPROTO.values():
        with NFTables(nfgen_family=family) as nft:
            for handle in _transit_rule_handles(nft, family) or ():
                try:
                    nft.rule("del", table="filter", chain="FORWARD", handle=handle)
                    removed += 1
                except NetlinkError as exc:
                    if exc.code != errno.ENOENT:
                        raise
    return removed
