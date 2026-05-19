# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Read-only kernel inventory for Node Agent substrate audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from node_agent import vxlan
from node_agent.kernel_constants import IFF_UP
from node_agent.namespace_runner import run_in_host_namespace, run_in_pod_namespace


@dataclass(frozen=True, slots=True)
class InterfaceInventory:
    namespace: str
    ifname: str
    exists: bool
    index: int | None = None
    admin_up: bool | None = None
    operstate: str | None = None
    mtu: int | None = None
    mac: str | None = None
    raw: str = ""


@dataclass(frozen=True, slots=True)
class QdiscInventory:
    namespace: str
    ifname: str
    kinds: tuple[str, ...] = ()
    netem_delay_us: int | None = None
    tbf_rate_bps: int | None = None
    raw: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VxlanInventory:
    vni: int
    ifname: str
    exists: bool
    local_ip: str | None = None
    remote_ip: str | None = None
    dst_port: int | None = None
    mtu: int | None = None
    raw: str = ""


@dataclass(frozen=True, slots=True)
class KernelInventory:
    interfaces: tuple[InterfaceInventory, ...] = field(default_factory=tuple)
    qdiscs: tuple[QdiscInventory, ...] = field(default_factory=tuple)
    vxlans: tuple[VxlanInventory, ...] = field(default_factory=tuple)


def _attrs(obj: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key, value in obj.get("attrs", []):
        attrs[key] = value
    return attrs


def _link_inventory(ipr, *, namespace: str, ifname: str) -> InterfaceInventory:
    idxs = ipr.link_lookup(ifname=ifname)
    if not idxs:
        return InterfaceInventory(namespace=namespace, ifname=ifname, exists=False)
    link = ipr.get_links(idxs[0])[0]
    attrs = _attrs(link)
    flags = int(link.get("flags", 0))
    return InterfaceInventory(
        namespace=namespace,
        ifname=ifname,
        exists=True,
        index=idxs[0],
        admin_up=bool(flags & IFF_UP),
        operstate=attrs.get("IFLA_OPERSTATE"),
        mtu=attrs.get("IFLA_MTU"),
        mac=attrs.get("IFLA_ADDRESS"),
        raw=repr(link),
    )


def host_interface(ifname: str) -> InterfaceInventory:
    return run_in_host_namespace(lambda ipr: _link_inventory(ipr, namespace="host", ifname=ifname))


def pod_interface(pid: int, ifname: str) -> InterfaceInventory:
    return run_in_pod_namespace(
        pid,
        lambda ipr: _link_inventory(ipr, namespace=f"pid:{pid}", ifname=ifname),
    )


def _walk_values(obj: Any):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key), value
            yield from _walk_values(value)
    elif isinstance(obj, list | tuple):
        for item in obj:
            yield from _walk_values(item)


def _qdisc_inventory(ipr, *, namespace: str, ifname: str) -> QdiscInventory:
    idxs = ipr.link_lookup(ifname=ifname)
    if not idxs:
        return QdiscInventory(namespace=namespace, ifname=ifname)
    rows = ipr.get_qdiscs(index=idxs[0])
    kinds: list[str] = []
    raw: list[str] = []
    delay_us: int | None = None
    rate_bps: int | None = None
    for row in rows:
        kind = row.get_attr("TCA_KIND")
        if kind:
            kinds.append(kind)
        raw.append(repr(row))
        options = row.get_attr("TCA_OPTIONS")
        for key, value in _walk_values(options):
            lower = key.lower()
            if "delay" in lower and isinstance(value, int):
                delay_us = value
            if lower == "rate" and isinstance(value, int):
                rate_bps = value
    return QdiscInventory(
        namespace=namespace,
        ifname=ifname,
        kinds=tuple(kinds),
        netem_delay_us=delay_us,
        tbf_rate_bps=rate_bps,
        raw=tuple(raw),
    )


def pod_qdisc(pid: int, ifname: str) -> QdiscInventory:
    return run_in_pod_namespace(
        pid,
        lambda ipr: _qdisc_inventory(ipr, namespace=f"pid:{pid}", ifname=ifname),
    )


def vxlan_interface(vni: int) -> VxlanInventory:
    ifname, _, _ = vxlan._host_ifnames(vni)

    def _op(ipr):
        idxs = ipr.link_lookup(ifname=ifname)
        if not idxs:
            return VxlanInventory(vni=vni, ifname=ifname, exists=False)
        link = ipr.get_links(idxs[0])[0]
        attrs = _attrs(link)
        linkinfo = attrs.get("IFLA_LINKINFO") or {}
        info_attrs = _attrs(linkinfo)
        data = info_attrs.get("IFLA_INFO_DATA") or {}
        data_attrs = _attrs(data) if isinstance(data, dict) else {}
        return VxlanInventory(
            vni=vni,
            ifname=ifname,
            exists=True,
            local_ip=data_attrs.get("IFLA_VXLAN_LOCAL"),
            remote_ip=data_attrs.get("IFLA_VXLAN_GROUP"),
            dst_port=data_attrs.get("IFLA_VXLAN_PORT"),
            mtu=attrs.get("IFLA_MTU"),
            raw=repr(link),
        )

    return run_in_host_namespace(_op)


def collect(
    *,
    host_ifnames: tuple[str, ...] = (),
    pod_ifnames: tuple[tuple[int, str], ...] = (),
    qdisc_ifnames: tuple[tuple[int, str], ...] = (),
    vnis: tuple[int, ...] = (),
) -> KernelInventory:
    """Collect a bounded inventory of managed substrate resources."""
    return KernelInventory(
        interfaces=tuple(
            [host_interface(ifname) for ifname in host_ifnames]
            + [pod_interface(pid, ifname) for pid, ifname in pod_ifnames]
        ),
        qdiscs=tuple(pod_qdisc(pid, ifname) for pid, ifname in qdisc_ifnames),
        vxlans=tuple(vxlan_interface(vni) for vni in vnis),
    )
