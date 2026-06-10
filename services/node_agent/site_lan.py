# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Site LAN wiring: per-host bridge, member veths, VXLAN head-end replication.

A physical site's LAN is static, always-up terrestrial infrastructure created
during session wiring — never Scheduler-dispatched, never a visibility link.
Every member pod's terr0 is a veth port on the site's bridge; hosts that share
a site are joined by a VXLAN port with head-end replication so IGP multicast
(hellos, DIS election) crosses hosts. Single-member sites get a one-port
bridge: one shared path, no mode split, and the bridge is also the future
attachment point for a real physical uplink interface.

Kernel layout per host, per site:

    Host namespace:
      sl<vni>            Linux bridge (this host's segment of the site LAN)
        ├─ sm<vni><i>    veth host-end per LOCAL member pod (bridge port)
        └─ sv<vni>       VXLAN port (only when members span hosts), learning
                         on, all-zeros FDB entry per peer host (head-end
                         replication for BUM traffic)
    Pod namespace (member i):
      terr0              veth pod-end, carries the resolved terr0 addresses

MTU is uniform at (platform veth MTU - VXLAN overhead) regardless of
placement: an L2 segment whose MTU depended on which hosts the scheduler
picked would let placement leak into protocol-visible behavior.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from dataclasses import dataclass

from nodalarc.runtime_naming import (
    site_lan_bridge_name,
    site_lan_member_host_ifname,
    site_lan_member_pod_ifname,
    site_lan_vxlan_name,
)

from node_agent.namespace_ops import _get_host_ns_fd, _in_namespace, _libc, _ns_lock
from node_agent.vxlan import VXLAN_DST_PORT, VXLAN_OVERHEAD_BYTES

log = logging.getLogger(__name__)

_CLONE_NEWNET = 0x40000000
_ALL_ZEROS_MAC = "00:00:00:00:00:00"

# br_netfilter (bridge-nf-call-iptables=1, standard on Kubernetes hosts) runs
# bridged frames through the host's iptables FORWARD chain, so a host-level
# policy such as Docker's FORWARD DROP silently discards site-LAN transit.
# ARP is exempt from br_netfilter, which produces the trap signature: ARP
# resolves across the LAN while every IP frame dies. The site LAN is emulated
# L2 substrate between session pods; host firewall policy governs host and
# cluster traffic, not this transit. The per-bridge nf_call_iptables flag
# cannot opt out (the kernel ORs it with the global sysctl), so the agent
# pins ACCEPT rules for frames entering its reserved bridge-port name
# namespaces (site_lan_member_host_ifname "sm…", site_lan_vxlan_name "sv…").
_SITE_LAN_TRANSIT_COMMENT = "nodalarc-site-lan-transit"
_SITE_LAN_TRANSIT_RULES: tuple[tuple[str, ...], ...] = tuple(
    (
        "-m",
        "physdev",
        "--physdev-is-bridged",
        "--physdev-in",
        wildcard,
        "-m",
        "comment",
        "--comment",
        _SITE_LAN_TRANSIT_COMMENT,
        "-j",
        "ACCEPT",
    )
    for wildcard in ("sm+", "sv+")
)
_FIREWALL_BINARIES = ("iptables", "ip6tables")


def _host_firewall(binary: str, args: tuple[str, ...]) -> subprocess.CompletedProcess:
    # The agent container has its own netns; firewall state is the host's.
    return subprocess.run(
        ["nsenter", "--net=/proc/1/ns/net", binary, *args],
        capture_output=True,
        text=True,
    )


def ensure_site_lan_transit() -> None:
    """Pin host-firewall ACCEPT rules for site-LAN bridged transit.

    Idempotent: each rule is checked before insertion. Raises when a rule
    cannot be installed — a site LAN whose transit the host may police is a
    wiring failure, not a degraded success.
    """
    for binary in _FIREWALL_BINARIES:
        for rule in _SITE_LAN_TRANSIT_RULES:
            check = _host_firewall(binary, ("-C", "FORWARD", *rule))
            if check.returncode == 0:
                continue
            insert = _host_firewall(binary, ("-I", "FORWARD", "1", *rule))
            if insert.returncode != 0:
                raise RuntimeError(
                    f"failed to pin site LAN transit rule via {binary}: "
                    f"{insert.stderr.strip() or insert.stdout.strip()}"
                )
    log.info("Site LAN transit rules pinned in host FORWARD chain")


def remove_site_lan_transit() -> None:
    """Remove the pinned transit rules (cleanup path). Best-effort by design:
    cleanup must not fail because a rule is already gone."""
    for binary in _FIREWALL_BINARIES:
        for rule in _SITE_LAN_TRANSIT_RULES:
            while _host_firewall(binary, ("-C", "FORWARD", *rule)).returncode == 0:
                delete = _host_firewall(binary, ("-D", "FORWARD", *rule))
                if delete.returncode != 0:
                    log.warning(
                        "Could not remove site LAN transit rule via %s: %s",
                        binary,
                        delete.stderr.strip(),
                    )
                    break


@dataclass(frozen=True)
class MemberPort:
    """One local member pod's attachment to the site bridge."""

    node_id: str
    pid: int
    host_ifname: str
    pod_ifname: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class SiteLanPlan:
    """Everything this host must wire for one site LAN. Pure data."""

    site_id: str
    vni: int
    bridge: str
    mtu: int
    local_members: tuple[MemberPort, ...]
    vxlan_ifname: str | None
    vxlan_local_ip: str | None
    peer_host_ips: tuple[str, ...] = ()


def plan_site_lan(
    site_id: str,
    spec: dict,
    *,
    nodes: dict[str, dict],
    pid_map: dict[str, int],
    local_node: str,
    local_ip: str,
    base_mtu: int,
) -> SiteLanPlan | None:
    """Derive this host's wiring plan for one site LAN from manifest truth.

    Returns None when no member lives on this host. Member indices come from
    the manifest's member order, so interface names are identical no matter
    which host computes them.
    """
    if spec.get("uplink"):
        # Schema slot for the real-world attachment exists ahead of the
        # wiring; encountering one must never be a silent no-op.
        raise RuntimeError(
            f"site LAN {site_id!r} declares a physical uplink, which this Node Agent "
            "does not implement yet"
        )
    members = spec["members"]
    vni = int(spec["vni"])

    local_ports: list[MemberPort] = []
    peer_hosts: dict[str, str] = {}
    for index, member in enumerate(members):
        node_id = member["node_id"]
        if member["k3s_node"] != local_node:
            peer_hosts[member["k3s_node"]] = member["host_ip"]
            continue
        pid = pid_map.get(node_id, 0)
        if pid == 0:
            # The manifest places this member here but the pod is not local —
            # the caller records the divergence as a wiring failure.
            raise RuntimeError(
                f"site LAN {site_id!r} member {node_id!r} is placed on this host "
                "but has no local pod"
            )
        addresses = tuple(nodes.get(node_id, {}).get("terrestrial", {}).get("addresses", ()))
        if not addresses:
            raise RuntimeError(f"site LAN {site_id!r} member {node_id!r} has no terr0 addresses")
        local_ports.append(
            MemberPort(
                node_id=node_id,
                pid=pid,
                host_ifname=site_lan_member_host_ifname(vni, index),
                pod_ifname=site_lan_member_pod_ifname(vni, index),
                addresses=addresses,
            )
        )

    if not local_ports:
        return None

    spans_hosts = bool(peer_hosts)
    if spans_hosts and not local_ip:
        raise RuntimeError(
            f"site LAN {site_id!r} spans hosts but this agent has no HOST_IP for "
            "the VXLAN local endpoint"
        )
    return SiteLanPlan(
        site_id=site_id,
        vni=vni,
        bridge=site_lan_bridge_name(vni),
        mtu=base_mtu - VXLAN_OVERHEAD_BYTES,
        local_members=tuple(local_ports),
        vxlan_ifname=site_lan_vxlan_name(vni) if spans_hosts else None,
        vxlan_local_ip=local_ip if spans_hosts else None,
        peer_host_ips=tuple(sorted(peer_hosts.values())),
    )


def wire_site_lan(plan: SiteLanPlan) -> None:
    """Execute one site LAN plan: bridge, member ports, VXLAN replication.

    Stale interfaces were removed by phase0 cleanup (site LAN names are
    managed host ifnames), so creation starts from clean state — Case A.
    """
    from pyroute2 import IPRoute

    pod_ns_fds: dict[str, int] = {}
    try:
        for port in plan.local_members:
            pod_ns_fds[port.node_id] = os.open(f"/proc/{port.pid}/ns/net", os.O_RDONLY)

        with _ns_lock:
            host_fd = _get_host_ns_fd()
            ret = _libc.setns(host_fd, _CLONE_NEWNET)
            if ret != 0:
                errno = ctypes.get_errno()
                raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")

            ipr = IPRoute()
            try:
                _wire_host_side(ipr, plan, pod_ns_fds)
            finally:
                ipr.close()
    finally:
        for fd in pod_ns_fds.values():
            os.close(fd)

    for port in plan.local_members:
        _configure_member_pod(port)

    log.info(
        "Site LAN %s wired: bridge=%s members=%d vni=%d peers=%d mtu=%d",
        plan.site_id,
        plan.bridge,
        len(plan.local_members),
        plan.vni,
        len(plan.peer_host_ips),
        plan.mtu,
    )


def _wire_host_side(ipr, plan: SiteLanPlan, pod_ns_fds: dict[str, int]) -> None:
    bridge_idx = _ensure_link(ipr, plan.bridge, kind="bridge", mtu=plan.mtu)

    for port in plan.local_members:
        _ensure_veth(ipr, port.host_ifname, port.pod_ifname, mtu=plan.mtu)
        host_idx = ipr.link_lookup(ifname=port.host_ifname)[0]
        ipr.link("set", index=host_idx, master=bridge_idx)
        ipr.link("set", index=host_idx, state="up")
        pod_idx = ipr.link_lookup(ifname=port.pod_ifname)
        if not pod_idx:
            raise RuntimeError(f"veth pod-end {port.pod_ifname} not found after create")
        ipr.link("set", index=pod_idx[0], net_ns_fd=pod_ns_fds[port.node_id])

    if plan.vxlan_ifname is not None:
        vxlan_idx = _ensure_link(
            ipr,
            plan.vxlan_ifname,
            kind="vxlan",
            mtu=plan.mtu,
            vxlan_id=plan.vni,
            vxlan_local=plan.vxlan_local_ip,
            vxlan_port=VXLAN_DST_PORT,
            vxlan_learning=True,
        )
        ipr.link("set", index=vxlan_idx, master=bridge_idx)
        ipr.link("set", index=vxlan_idx, state="up")
        for peer_ip in plan.peer_host_ips:
            # Head-end replication: flood BUM frames (IGP hellos included)
            # to every peer host carrying members of this site.
            ipr.fdb(
                "append",
                ifindex=vxlan_idx,
                lladdr=_ALL_ZEROS_MAC,
                dst=peer_ip,
            )

    ipr.link("set", index=bridge_idx, state="up")


def _ensure_link(ipr, ifname: str, *, kind: str, mtu: int, **kwargs) -> int:
    # Case C: partial kernel state from a prior attempt is cleaned, then the
    # link is wired from scratch — the same posture as every other wiring op.
    stale = ipr.link_lookup(ifname=ifname)
    if stale:
        log.info("Cleaning stale %s before site LAN create", ifname)
        ipr.link("del", index=stale[0])
    ipr.link("add", ifname=ifname, kind=kind, **kwargs)
    idx = ipr.link_lookup(ifname=ifname)[0]
    ipr.link("set", index=idx, mtu=mtu)
    return idx


def _ensure_veth(ipr, host_ifname: str, pod_ifname: str, *, mtu: int) -> None:
    for stale_name in (host_ifname, pod_ifname):
        stale = ipr.link_lookup(ifname=stale_name)
        if stale:
            log.info("Cleaning stale %s before site LAN create", stale_name)
            ipr.link("del", index=stale[0])
    ipr.link("add", ifname=host_ifname, kind="veth", peer={"ifname": pod_ifname})
    for name in (host_ifname, pod_ifname):
        idx = ipr.link_lookup(ifname=name)
        if idx:
            ipr.link("set", index=idx[0], mtu=mtu)


def _configure_member_pod(port: MemberPort) -> None:
    def _op(ns_ipr) -> None:
        links = ns_ipr.link_lookup(ifname=port.pod_ifname)
        if not links:
            raise RuntimeError(
                f"site LAN pod interface {port.pod_ifname} missing in ns of {port.node_id}"
            )
        idx = links[0]
        # A stale terr0 from a prior partial attempt blocks the rename;
        # remove it — its replacement is the freshly bridged veth.
        stale = ns_ipr.link_lookup(ifname="terr0")
        if stale and stale[0] != idx:
            ns_ipr.link("del", index=stale[0])
        ns_ipr.link("set", index=idx, ifname="terr0")
        for addr in port.addresses:
            ip_addr, prefixlen = addr.split("/")
            try:
                ns_ipr.addr("add", index=idx, address=ip_addr, prefixlen=int(prefixlen))
            except Exception as exc:
                # FRR zebra configures terr0's address from frr.conf the
                # moment the interface exists — losing that race means the
                # desired state is already true.
                if getattr(exc, "code", None) == 17 or (exc.args and exc.args[0] == 17):
                    continue
                raise
        ns_ipr.link("set", index=idx, state="up")

    _in_namespace(port.pid, _op)
