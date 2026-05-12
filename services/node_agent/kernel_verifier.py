# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""MVP kernel postcondition verification for Node Agent operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from node_agent import vxlan
from node_agent.namespace_runner import run_in_host_namespace, run_in_pod_namespace


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


def verify_host_interface_state(ifname: str, *, admin_up: bool | None = None) -> Proof:
    def _op(ipr):
        return _link_rows(ipr, ifname)

    rows = run_in_host_namespace(_op)
    if not rows:
        return Proof.fail(f"host interface {ifname} missing")
    flags = rows[0]["flags"]
    is_up = bool(flags & 0x1)
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
    elif isinstance(obj, list | tuple):
        for item in obj:
            yield from _walk_values(item)


def _extract_delay_us(rows: list[dict[str, Any]]) -> int | None:
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
        import re

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
        import re

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

    expected_delay_us = int(round(delay_ms * 1000))
    actual_delay_us = _extract_delay_us(rows)
    if actual_delay_us is None:
        return Proof.fail(f"cannot parse netem delay for {ifname}", *evidence)
    if abs(actual_delay_us - expected_delay_us) > 1:
        return Proof.fail(
            f"netem delay mismatch on {ifname}",
            f"expected_us={expected_delay_us}",
            f"actual_us={actual_delay_us}",
            *evidence,
        )

    if rate_mbps is not None:
        expected_rate = int(rate_mbps * 1_000_000)
        if expected_rate > 0xFFFFFFFF:
            expected_rate = 0xFFFFFFFF
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

    return Proof.ok(
        f"qdisc verified on {ifname}",
        f"delay_us={actual_delay_us}",
        *(evidence[:4]),
    )


def _linkinfo_attrs(raw_link) -> dict[str, Any]:
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


def verify_vxlan(vni: int, *, local_ip: str, remote_ip: str) -> Proof:
    vxlan_if, _, _ = vxlan._host_ifnames(vni)

    def _op(ipr):
        idxs = ipr.link_lookup(ifname=vxlan_if)
        if not idxs:
            return None
        return ipr.get_links(idxs[0])[0]

    link = run_in_host_namespace(_op)
    if link is None:
        return Proof.fail(f"VXLAN {vxlan_if} missing", f"vni={vni}")
    attrs = _linkinfo_attrs(link)
    evidence = (f"ifname={vxlan_if}", f"vni={vni}", repr(link))
    if attrs.get("IFLA_INFO_KIND") != "vxlan":
        return Proof.fail(f"{vxlan_if} is not vxlan", *evidence)
    checks = {
        "IFLA_VXLAN_ID": vni,
        "IFLA_VXLAN_LOCAL": local_ip,
        "IFLA_VXLAN_GROUP": remote_ip,
        "IFLA_VXLAN_PORT": vxlan.VXLAN_DST_PORT,
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


def verify_vxlan_absent(vni: int) -> Proof:
    vxlan_if, veth_host, _ = vxlan._host_ifnames(vni)
    proofs = [verify_host_interface_absent(vxlan_if), verify_host_interface_absent(veth_host)]
    failures = [p for p in proofs if not p.verified]
    if failures:
        evidence = tuple(item for proof in proofs for item in proof.evidence)
        return Proof.fail("VXLAN cleanup proof failed", *evidence)
    return Proof.ok(f"VXLAN VNI {vni} cleaned", vxlan_if, veth_host)


def verify_mirred(src_ifname: str, dst_ifname: str) -> Proof:
    def _op(ipr):
        src = ipr.link_lookup(ifname=src_ifname)
        dst = ipr.link_lookup(ifname=dst_ifname)
        if not src:
            return "missing-src", []
        if not dst:
            return "missing-dst", []
        try:
            filters = ipr.get_filters(index=src[0], parent=0xFFFF0000)
        except Exception as exc:
            return f"filter-read-error:{exc}", []
        return "ok", [repr(f) for f in filters]

    status, filters = run_in_host_namespace(_op)
    if status != "ok":
        return Proof.fail(f"mirred proof failed {src_ifname}->{dst_ifname}", status)
    joined = "\n".join(filters)
    if "mirred" not in joined:
        return Proof.fail(f"missing mirred filter {src_ifname}->{dst_ifname}", joined)
    return Proof.ok(f"mirred verified {src_ifname}->{dst_ifname}", joined[:1000])
