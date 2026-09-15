# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MVP kernel postcondition verification for Node Agent operations."""

from __future__ import annotations

import re
import socket
import struct
from dataclasses import dataclass, field
from typing import Any

from nodalarc.runtime_naming import vxlan_host_ifnames
from nodalarc.vxlan import VXLAN_DST_PORT
from pyroute2.netlink.rtnl import TC_H_INGRESS

from node_agent.kernel_constants import (
    IFF_UP,
    MPLS_INPUT_ENABLED,
    NETEM_TICK_TOLERANCE,
    TBF_RATE32_MAX_BPS,
    mpls_input_sysctl,
)
from node_agent.namespace_runner import run_in_host_namespace, run_in_pod_namespace
from node_agent.tc_units import delay_ms_to_netem_us, netem_us_to_ticks


@dataclass(frozen=True)
class Proof:
    verified: bool
    summary: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @staticmethod
    def ok(summary: str, *evidence: str) -> Proof:
        return Proof(True, summary, tuple(evidence))

    @staticmethod
    def fail(summary: str, *evidence: str) -> Proof:
        return Proof(False, summary, tuple(evidence))


def _link_rows(ipr, ifname: str) -> list[dict[str, Any]]:
    rows = []
    for idx in ipr.link_lookup(ifname=ifname):
        link = ipr.get_links(idx)[0]
        rows.append(
            {
                "ifname": ifname,
                "index": idx,
                "flags": int(link.get("flags", 0)),
                "operstate": link.get_attr("IFLA_OPERSTATE"),
                "raw": repr(link),
            }
        )
    return rows


def verify_pod_interface_exists(pid: int, ifname: str) -> Proof:
    def _op(ipr):
        return _link_rows(ipr, ifname)

    rows = run_in_pod_namespace(pid, _op)
    if not rows:
        return Proof.fail(f"pod interface {ifname} missing", f"pid={pid}")
    return Proof.ok(f"pod interface {ifname} exists", f"pid={pid}", rows[0]["raw"])


def verify_mpls_input(pid: int, ifname: str) -> Proof:
    """Read a pod interface's MPLS input switch back from the kernel.

    The switch lives under the pod namespace's own sysctl tree, so the read
    happens inside that namespace. An unreadable switch is not evidence of
    anything and fails with the error kept.
    """
    key = mpls_input_sysctl(ifname)
    path = "/proc/sys/" + key.replace(".", "/")

    def _read(_ipr) -> str:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip()

    try:
        observed = run_in_pod_namespace(pid, _read)
    except OSError as exc:
        return Proof.fail(
            f"mpls input unreadable on {ifname}",
            f"device={ifname}",
            f"key={key}",
            f"error={exc.strerror or exc}",
        )
    if observed != MPLS_INPUT_ENABLED:
        return Proof.fail(
            f"mpls input disabled on {ifname}",
            f"device={ifname}",
            f"key={key}",
            f"expected={MPLS_INPUT_ENABLED}",
            f"observed={observed}",
        )
    return Proof.ok(
        f"mpls input enabled on {ifname}",
        f"device={ifname}",
        f"key={key}",
        f"observed={observed}",
    )


def verify_host_interface_state(ifname: str, *, admin_up: bool | None = None) -> Proof:
    def _op(ipr):
        return _link_rows(ipr, ifname)

    rows = run_in_host_namespace(_op)
    if not rows:
        return Proof.fail(f"host interface {ifname} missing")
    flags = rows[0]["flags"]
    is_up = bool(flags & IFF_UP)
    if admin_up is not None and is_up != admin_up:
        want = "UP" if admin_up else "DOWN"
        got = "UP" if is_up else "DOWN"
        return Proof.fail(
            f"host interface {ifname} admin state mismatch",
            f"expected={want}",
            f"actual={got}",
            rows[0]["raw"],
        )
    return Proof.ok(f"host interface {ifname} state verified", rows[0]["raw"])


def verify_host_interface_absent(ifname: str) -> Proof:
    def _op(ipr):
        return _link_rows(ipr, ifname)

    rows = run_in_host_namespace(_op)
    if rows:
        return Proof.fail(f"host interface {ifname} still exists", rows[0]["raw"])
    return Proof.ok(f"host interface {ifname} absent")


def _qdisc_rows(ipr, ifname: str) -> list[dict[str, Any]]:
    idxs = ipr.link_lookup(ifname=ifname)
    if not idxs:
        raise FileNotFoundError(f"Interface {ifname} not found")
    rows = []
    for qdisc in ipr.get_qdiscs(index=idxs[0]):
        rows.append(
            {
                "kind": qdisc.get_attr("TCA_KIND"),
                "options": qdisc.get_attr("TCA_OPTIONS"),
                "raw": repr(qdisc),
            }
        )
    return rows


def _walk_values(obj: Any):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key), value
            yield from _walk_values(value)
    elif isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], str):
        yield obj[0], obj[1]
        yield from _walk_values(obj[1])
    elif isinstance(obj, list | tuple):
        for item in obj:
            yield from _walk_values(item)


def _extract_delay_ticks(rows: list[dict[str, Any]]) -> int | None:
    for row in rows:
        if row["kind"] != "netem":
            continue
        for key, value in _walk_values(row["options"]):
            if "delay" in key.lower() and isinstance(value, int):
                return value
        raw = row["raw"]
        # Last-resort pyroute2 representation fallback. The canonical path is
        # parsed TCA_OPTIONS above; this keeps proof usable across pyroute2
        # minor versions that stringify netem options differently.

        m = re.search(r"delay['\"]?:\s*(\d+)", raw)
        if m:
            return int(m.group(1))
    return None


def _extract_rate_bps(rows: list[dict[str, Any]]) -> int | None:
    for row in rows:
        if row["kind"] != "tbf":
            continue
        for key, value in _walk_values(row["options"]):
            if key.lower() == "rate" and isinstance(value, int):
                return value

        m = re.search(r"rate['\"]?:\s*(\d+)", row["raw"])
        if m:
            return int(m.group(1))
    return None


def verify_qdisc(
    pid: int, ifname: str, *, delay_ms: float, rate_mbps: float | None = None
) -> Proof:
    def _op(ipr):
        return _qdisc_rows(ipr, ifname)

    try:
        rows = run_in_pod_namespace(pid, _op)
    except Exception as exc:
        return Proof.fail(f"qdisc proof failed for {ifname}", f"pid={pid}", str(exc))

    kinds = {row["kind"] for row in rows}
    evidence = [f"pid={pid}", f"ifname={ifname}", *(row["raw"] for row in rows)]
    if "tbf" not in kinds:
        return Proof.fail(f"missing tbf qdisc on {ifname}", *evidence)
    if "netem" not in kinds:
        return Proof.fail(f"missing netem qdisc on {ifname}", *evidence)

    if delay_ms >= 0:
        # Netem delay is configured in integer microseconds, but the kernel
        # reports it in tc scheduler ticks. Use the same normalization as the
        # mutator, then mirror pyroute2's encoder so proof compares the exact
        # kernel value.
        expected_delay_us = delay_ms_to_netem_us(delay_ms)
        expected_delay_ticks = netem_us_to_ticks(expected_delay_us)
        actual_delay_ticks = _extract_delay_ticks(rows)
        if actual_delay_ticks is None:
            return Proof.fail(f"cannot parse netem delay for {ifname}", *evidence)
        if abs(actual_delay_ticks - expected_delay_ticks) > NETEM_TICK_TOLERANCE:
            return Proof.fail(
                f"netem delay mismatch on {ifname}",
                f"expected_us={expected_delay_us}",
                f"expected_ticks={expected_delay_ticks}",
                f"actual_ticks={actual_delay_ticks}",
                *evidence,
            )
    # delay_ms < 0 is the explicit do-not-assert sentinel: the prover has no
    # commanded netem value for this link (e.g. a Scheduler instance that has
    # not dispatched it). Shaping presence and rate are still proven; comparing
    # the delay against an invented expectation would report normal
    # latency-update cadence as kernel divergence.

    if rate_mbps is not None:
        expected_rate = int(rate_mbps * 1_000_000)
        if expected_rate > TBF_RATE32_MAX_BPS:
            expected_rate = TBF_RATE32_MAX_BPS
        actual_rate = _extract_rate_bps(rows)
        if actual_rate is None:
            return Proof.fail(f"cannot parse tbf rate for {ifname}", *evidence)
        if actual_rate != expected_rate:
            return Proof.fail(
                f"tbf rate mismatch on {ifname}",
                f"expected_bps={expected_rate}",
                f"actual_bps={actual_rate}",
                *evidence,
            )

    if delay_ms < 0:
        return Proof.ok(f"qdisc verified on {ifname}; netem delay not asserted", *evidence)
    return Proof.ok(
        f"qdisc verified on {ifname}",
        f"delay_us={expected_delay_us}",
        f"delay_ticks={actual_delay_ticks}",
        *evidence,
    )


def linkinfo_attrs(raw_link) -> dict[str, Any]:
    linkinfo = raw_link.get_attr("IFLA_LINKINFO")
    if not linkinfo:
        return {}
    attrs = {}
    for name, value in linkinfo.get("attrs", []):
        attrs[name] = value
    data = attrs.get("IFLA_INFO_DATA")
    if isinstance(data, dict):
        for name, value in data.get("attrs", []):
            attrs[name] = value
    return attrs


# --- Lock-free proof primitives -------------------------------------------
#
# Each primitive proves one fact through an IPRoute the caller already holds
# in the right namespace. They take no lock, so creation can prove an
# existing link while it holds _ns_lock, and the locked verify_* wrappers
# below serve every other caller. One implementation of each proof.

# TCA_MIRRED_PARMS.eaction for "redirect to egress of ifindex".
_TCA_EGRESS_REDIR = 1
# NETNSA_NSID the kernel answers when it has assigned no id to a namespace.
_NETNSID_UNASSIGNED = 0xFFFFFFFF


def link_attrs(ipr, ifname: str):
    """The link message for ``ifname``, or None when it does not exist."""
    idxs = ipr.link_lookup(ifname=ifname)
    if not idxs:
        return None
    return ipr.get_links(idxs[0])[0]


@dataclass(frozen=True, slots=True)
class PodVethEnd:
    """What a pod namespace holds under a link's interface name."""

    ifindex: int
    kind: str | None
    peer_ifindex: int | None
    mtu: int | None


def pod_veth_end(pid: int, ifname: str) -> PodVethEnd | None:
    """Read the pod end of a link before any host-namespace work.

    Entering the pod namespace takes the same non-reentrant lock the host
    work holds, so a caller reads the pod end first and proves it against
    the host device afterwards.
    """

    def _read(ns_ipr):
        link = link_attrs(ns_ipr, ifname)
        if link is None:
            return None
        return PodVethEnd(
            ifindex=int(link["index"]),
            kind=linkinfo_attrs(link).get("IFLA_INFO_KIND"),
            peer_ifindex=link.get_attr("IFLA_LINK"),
            mtu=link.get_attr("IFLA_MTU"),
        )

    return run_in_pod_namespace(pid, _read)


def prove_vxlan_device(ipr, vxlan_if: str, *, vni: int, local_ip: str, remote_ip: str) -> Proof:
    """The tunnel device exists and carries exactly the requested endpoints."""
    link = link_attrs(ipr, vxlan_if)
    if link is None:
        return Proof.fail(f"VXLAN {vxlan_if} missing", f"vni={vni}")
    attrs = linkinfo_attrs(link)
    evidence = (f"ifname={vxlan_if}", f"vni={vni}", repr(link))
    if attrs.get("IFLA_INFO_KIND") != "vxlan":
        return Proof.fail(f"{vxlan_if} is not vxlan", *evidence)
    checks = {
        "IFLA_VXLAN_ID": vni,
        "IFLA_VXLAN_LOCAL": local_ip,
        "IFLA_VXLAN_GROUP": remote_ip,
        "IFLA_VXLAN_PORT": VXLAN_DST_PORT,
    }
    for key, expected in checks.items():
        actual = attrs.get(key)
        if actual != expected:
            return Proof.fail(
                f"VXLAN {vxlan_if} {key} mismatch",
                f"expected={expected}",
                f"actual={actual}",
                *evidence,
            )
    return Proof.ok(f"VXLAN {vxlan_if} verified", *evidence[:2])


def netnsid_of(ipr, ns_fd: int) -> int | None:
    """The id this namespace's kernel view assigns to the namespace behind ``ns_fd``.

    None when the kernel has assigned none, which means no device here has a
    peer in that namespace.
    """
    from pyroute2.netlink import NLM_F_ACK, NLM_F_REQUEST
    from pyroute2.netlink.rtnl import RTM_GETNSID
    from pyroute2.netlink.rtnl.nsidmsg import nsidmsg

    msg = nsidmsg()
    msg["attrs"] = [("NETNSA_FD", ns_fd)]
    msg["header"]["type"] = RTM_GETNSID
    msg["header"]["flags"] = NLM_F_REQUEST | NLM_F_ACK
    for reply in ipr.nlm_request_batch([msg]):
        nsid = reply.get_attr("NETNSA_NSID")
        if nsid is None or nsid == _NETNSID_UNASSIGNED:
            return None
        return int(nsid)
    return None


def prove_veth_peer(ipr, host_ifname: str, *, peer_ns_fd: int, peer_ifindex: int) -> Proof:
    """The host veth's peer is the given interface in the given namespace.

    The kernel names the peer namespace on the link (IFLA_LINK_NETNSID) and
    the peer's index inside it (IFLA_LINK); both must match the namespace
    behind ``peer_ns_fd`` and ``peer_ifindex``. Index numbers alone prove
    nothing, since they are local to each namespace.

    The link is read first: the kernel assigns the peer namespace its id in
    this namespace while reporting the link, so the id lookup follows the dump.
    """
    link = link_attrs(ipr, host_ifname)
    if link is None:
        return Proof.fail(f"veth {host_ifname} missing")
    evidence = (f"ifname={host_ifname}", repr(link))
    if linkinfo_attrs(link).get("IFLA_INFO_KIND") != "veth":
        return Proof.fail(f"{host_ifname} is not veth", *evidence)
    peer_nsid = link.get_attr("IFLA_LINK_NETNSID")
    if peer_nsid is None:
        return Proof.fail(f"veth {host_ifname} peer is not in another namespace", *evidence)
    expected_nsid = netnsid_of(ipr, peer_ns_fd)
    if expected_nsid is None or int(peer_nsid) != expected_nsid:
        return Proof.fail(
            f"veth {host_ifname} peer namespace mismatch",
            f"expected_nsid={expected_nsid}",
            f"actual_nsid={peer_nsid}",
            *evidence,
        )
    if link.get_attr("IFLA_LINK") != peer_ifindex:
        return Proof.fail(
            f"veth {host_ifname} peer index mismatch",
            f"expected_ifindex={peer_ifindex}",
            f"actual_ifindex={link.get_attr('IFLA_LINK')}",
            *evidence,
        )
    return Proof.ok(
        f"veth {host_ifname} peer verified",
        f"peer_nsid={expected_nsid}",
        f"peer_ifindex={peer_ifindex}",
    )


def prove_link_mtu(ipr, ifname: str, *, mtu: int) -> Proof:
    """The interface carries exactly the MTU NodalArc set on it at creation."""
    link = link_attrs(ipr, ifname)
    if link is None:
        return Proof.fail(f"{ifname} missing", f"device={ifname}")
    observed = link.get_attr("IFLA_MTU")
    if observed != mtu:
        return Proof.fail(
            f"{ifname} MTU mismatch",
            f"device={ifname}",
            f"expected={mtu}",
            f"observed={observed}",
        )
    return Proof.ok(f"{ifname} MTU verified", f"device={ifname}", f"mtu={mtu}")


def prove_link_admin_up(ipr, ifname: str) -> Proof:
    """The interface is administratively UP, the state NodalArc set on it at creation.

    Only NodalArc-owned host devices are proven this way; a pod interface's
    administrative state belongs to the workload after creation.
    """
    link = link_attrs(ipr, ifname)
    if link is None:
        return Proof.fail(f"{ifname} missing", f"device={ifname}")
    if not int(link.get("flags", 0)) & IFF_UP:
        return Proof.fail(
            f"{ifname} admin state mismatch", f"device={ifname}", "expected=UP", "observed=DOWN"
        )
    return Proof.ok(f"{ifname} admin UP verified", f"device={ifname}")


class KernelStateConflict(RuntimeError):
    """Kernel state exists under a link's names and is not proven to be that link.

    The refusing step created, deleted and replaced nothing. A caller may
    already have changed interface state on the way here: the documented
    LinkUp order brings host veths admin UP before redirects are installed,
    and that state is left as it stands. The evidence names what exists and
    which proof failed, so the refusal is attributable to the substrate
    rather than to the link being requested.
    """

    def __init__(
        self,
        subject: str,
        present: tuple[str, ...],
        failures: tuple[str, ...],
        failure_evidence: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        """``failures`` are the failed proofs' summaries; ``failure_evidence`` holds
        each one's evidence in the same order, so the values a proof compared
        (device, expected, observed) reach the caller and the outward reply."""
        padded = failure_evidence + ((),) * (len(failures) - len(failure_evidence))
        rendered = [
            f"{summary} [{', '.join(evidence)}]" if evidence else summary
            for summary, evidence in zip(failures, padded, strict=True)
        ]
        super().__init__(
            f"{subject}: existing kernel state is not the requested link "
            f"(present: {', '.join(present)}; failed: {'; '.join(rendered)})"
        )
        self.subject = subject
        self.present = present
        self.failures = failures
        self.failure_evidence = padded


def reuse_or_refuse(
    *,
    subject: str,
    absent: bool,
    complete: bool,
    proofs: tuple[Proof, ...],
    evidence: tuple[str, ...],
) -> bool:
    """The one policy for kernel state found under a link's names.

    Absent: create (False). Complete and every proof verified: reuse (True).
    Partial, conflicting or unprovable: refuse, and nothing is taken over.
    ``evidence`` describes what exists for the refusal; it decides nothing.
    """
    if absent:
        return False
    failed = tuple(proof for proof in proofs if not proof.verified)
    failures = tuple(proof.summary for proof in failed)
    failure_evidence = tuple(proof.evidence for proof in failed)
    if not complete:
        raise KernelStateConflict(
            subject, evidence, ("incomplete link",) + failures, ((),) + failure_evidence
        )
    if failures:
        raise KernelStateConflict(subject, evidence, failures, failure_evidence)
    return True


def ingress_qdisc_kind(ipr, ifindex: int) -> str | None:
    """The kind of the qdisc holding an interface's ingress side, None when there is none.

    Both the classic ``ingress`` qdisc and ``clsact`` report the ingress parent.
    """
    for qdisc in ipr.get_qdiscs(index=ifindex):
        if qdisc["parent"] == TC_H_INGRESS:
            return str(qdisc.get_attr("TCA_KIND"))
    return None


# The u32 option attributes a match-all redirect rule carries, and those
# pyroute2 leaves undecoded (numbered per the kernel's TCA_U32_* enum).
_U32_RULE_ATTRS = frozenset({"TCA_U32_SEL", "TCA_U32_HASH", "TCA_U32_CLASSID", "TCA_U32_ACT"})
_U32_PCNT, _U32_FLAGS, _U32_PAD = 9, 11, 12
_U32_TERMINAL = 1
_ETH_P_ALL_INFO = socket.htons(0x0003)
# The filter-level attribute naming the chain a filter belongs to, which
# pyroute2 leaves undecoded; a four-byte chain index follows the nla header.
_TCA_CHAIN = 11
_TCA_CHAIN_NLA_LENGTH = 8


def _attr_items(node) -> list[tuple[str, Any]]:
    """The attributes of one netlink node; undecoded ones are named by their type number."""
    items: list[tuple[str, Any]] = []
    for name, value in node.get("attrs", []):
        if name == "UNKNOWN":
            items.append((f"UNKNOWN:{value['header']['type']}", value))
        else:
            items.append((name, value))
    return items


def _filter_chain(filt) -> int | None:
    """The chain index the kernel reports for one filter, None when it reports none."""
    for name, value in filt.get("attrs", []):
        if name != "UNKNOWN" or value["header"]["type"] != _TCA_CHAIN:
            continue
        raw = bytes(value.data[value.offset : value.offset + value.length])
        if len(raw) != _TCA_CHAIN_NLA_LENGTH:
            return None
        return struct.unpack("=I", raw[4:8])[0]
    return None


_U32_SELECTOR_OFFSET_FIELDS = ("off", "offshift", "offmask", "offoff", "hoff", "hmask")
_U32_KEY_FIELDS = ("key_mask", "key_val", "key_off", "key_offmask")


def _match_all_terminal(sel) -> bool:
    """The kernel reported exactly the one-key match-all terminal selector NodalArc installs.

    Every selector and key field must be present in the dump. A field the
    netlink reply did not carry is never read as zero, so a selector the
    kernel did not fully report is not a match-all selector.
    """
    if not isinstance(sel, dict):
        return False
    if any(field not in sel for field in ("flags", "nkeys", *_U32_SELECTOR_OFFSET_FIELDS)):
        return False
    if sel["nkeys"] != 1 or not sel["flags"] & _U32_TERMINAL:
        return False
    if any(sel[field] for field in _U32_SELECTOR_OFFSET_FIELDS):
        return False
    keys = sel.get("keys")
    if not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], dict):
        return False
    key = keys[0]
    if any(field not in key for field in _U32_KEY_FIELDS):
        return False
    return not any(key[field] for field in _U32_KEY_FIELDS)


def _single_redirect_to(act, dst_index: int) -> bool:
    """``act`` carries exactly one action: mirred, egress redirect to ``dst_index``."""
    entries = _attr_items(act)
    if len(entries) != 1 or not entries[0][0].startswith("TCA_ACT_PRIO_"):
        return False
    action = dict(_attr_items(entries[0][1]))
    if action.get("TCA_ACT_KIND") != "mirred":
        return False
    options = action.get("TCA_ACT_OPTIONS")
    if options is None:
        return False
    parms = [value for name, value in _attr_items(options) if name == "TCA_MIRRED_PARMS"]
    return len(parms) == 1 and (parms[0].get("eaction"), parms[0].get("ifindex")) == (
        _TCA_EGRESS_REDIR,
        dst_index,
    )


def _classify_ingress_filter(filt, dst_index: int) -> str:
    """Name what one ingress filter entry is, in the terms of the configuration NodalArc creates.

    ``u32-root`` and ``u32-table`` are the two entries a u32 dump lists beside
    its rules; ``redirect`` is the one rule; anything else is named by what it
    is and contests the path. Ingress traffic enters chain 0, so an entry in
    any other chain, or in no reported chain, never carries it.
    """
    chain = _filter_chain(filt)
    if chain != 0:
        return "chain-unknown" if chain is None else f"chain-{chain}"
    kind = dict(filt.get("attrs", [])).get("TCA_KIND")
    if kind != "u32":
        return f"{kind}-filter"
    options = dict(filt.get("attrs", [])).get("TCA_OPTIONS")
    if options is None:
        return "u32-root" if filt["handle"] == 0 else "u32-entry"
    names = [name for name, _ in _attr_items(options)]
    if names == ["TCA_U32_DIVISOR"]:
        return "u32-table"
    if (filt["info"] & 0xFFFF) != _ETH_P_ALL_INFO:
        return "u32-rule:protocol"
    extra = [
        name
        for name in names
        if name not in _U32_RULE_ATTRS
        and name not in (f"UNKNOWN:{_U32_PCNT}", f"UNKNOWN:{_U32_FLAGS}", f"UNKNOWN:{_U32_PAD}")
    ]
    if extra:
        return "u32-rule:" + ",".join(extra)
    attrs = dict(_attr_items(options))
    if not _match_all_terminal(attrs.get("TCA_U32_SEL")):
        return "u32-rule:selector"
    if "TCA_U32_ACT" not in attrs or not _single_redirect_to(attrs["TCA_U32_ACT"], dst_index):
        return "u32-rule:action"
    return "redirect"


def prove_mirred_redirect(ipr, src_ifname: str, dst_ifname: str) -> Proof:
    """``src`` carries exactly the ingress redirect NodalArc installs toward ``dst``.

    An ``ingress`` qdisc; in chain 0, one u32 rule at protocol all whose
    selector matches every packet and whose only action is a mirred egress
    redirect to ``dst``, the u32 root entry and one hash-table node; nothing
    else in any chain. Any other qdisc, chain, filter, selector, protocol or
    action leaves the path contested, which is never a complete match.
    """
    src = ipr.link_lookup(ifname=src_ifname)
    dst = ipr.link_lookup(ifname=dst_ifname)
    if not src:
        return Proof.fail(f"mirred proof failed {src_ifname}->{dst_ifname}", "missing-src")
    if not dst:
        return Proof.fail(f"mirred proof failed {src_ifname}->{dst_ifname}", "missing-dst")
    dst_index = dst[0]
    try:
        qdisc_kind = ingress_qdisc_kind(ipr, src[0])
        filters = list(ipr.get_filters(index=src[0], parent=TC_H_INGRESS))
    except Exception as exc:
        return Proof.fail(f"mirred proof failed {src_ifname}->{dst_ifname}", f"tc-read-error:{exc}")
    joined = "\n".join(repr(filt) for filt in filters)
    if qdisc_kind is None:
        return Proof.fail(f"missing ingress qdisc {src_ifname}", joined)
    if qdisc_kind != "ingress":
        return Proof.fail(
            f"mirred path contested {src_ifname}->{dst_ifname}", f"qdisc={qdisc_kind}", joined
        )
    observed = sorted(_classify_ingress_filter(filt, dst_index) for filt in filters)
    if observed == ["u32-root", "u32-table"] or not filters:
        return Proof.fail(f"missing mirred redirect {src_ifname}->{dst_ifname}", joined)
    if observed != ["redirect", "u32-root", "u32-table"]:
        return Proof.fail(
            f"mirred path contested {src_ifname}->{dst_ifname}",
            f"expected_ifindex={dst_index}",
            f"observed={observed}",
            joined,
        )
    return Proof.ok(
        f"mirred verified {src_ifname}->{dst_ifname}",
        f"dst_ifindex={dst_index}",
        joined,
    )


# --- Locked wrappers for callers that hold no namespace -------------------


def verify_vxlan(vni: int, *, local_ip: str, remote_ip: str) -> Proof:
    names = vxlan_host_ifnames(vni)
    return run_in_host_namespace(
        lambda ipr: prove_vxlan_device(
            ipr, names.tunnel, vni=vni, local_ip=local_ip, remote_ip=remote_ip
        )
    )


def verify_vxlan_absent(vni: int) -> Proof:
    names = vxlan_host_ifnames(vni)
    proofs = [
        verify_host_interface_absent(names.tunnel),
        verify_host_interface_absent(names.host_veth),
    ]
    failures = [p for p in proofs if not p.verified]
    if failures:
        evidence = tuple(item for proof in proofs for item in proof.evidence)
        return Proof.fail("VXLAN cleanup proof failed", *evidence)
    return Proof.ok(f"VXLAN VNI {vni} cleaned", names.tunnel, names.host_veth)


def verify_mirred(src_ifname: str, dst_ifname: str) -> Proof:
    return run_in_host_namespace(lambda ipr: prove_mirred_redirect(ipr, src_ifname, dst_ifname))
