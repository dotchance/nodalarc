# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime identity and Linux interface naming helpers.

Runtime node IDs reach Kubernetes labels/pod names and Node Agent host
interfaces. This module owns the bounded names so the resolver can validate the
exact identifiers that privileged code will later use. No service should derive
host-interface names by truncating node IDs directly.
"""

from __future__ import annotations

import hashlib
import re
from typing import NamedTuple

from nodalarc.vxlan import VNI_MAX, VNI_MIN

K8S_LABEL_VALUE_MAX = 63
LINUX_IFNAME_MAX = 15
_RUNTIME_NODE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def validate_runtime_node_id(node_id: str) -> None:
    """Fail if a runtime node ID cannot safely become a K8s label/pod name."""
    if len(node_id) > K8S_LABEL_VALUE_MAX:
        raise ValueError(
            f"runtime node_id {node_id!r} exceeds Kubernetes label value limit "
            f"({len(node_id)} > {K8S_LABEL_VALUE_MAX})"
        )
    if _RUNTIME_NODE_ID_RE.fullmatch(node_id) is None:
        raise ValueError(
            f"runtime node_id {node_id!r} must be lowercase DNS-label safe "
            "([a-z0-9-], no leading/trailing '-')"
        )


def _base36_2(index: int) -> str:
    if index < 0 or index >= len(_ALPHABET) ** 2:
        raise ValueError(f"interface index {index} is outside supported range 0..1295")
    return _ALPHABET[index // len(_ALPHABET)] + _ALPHABET[index % len(_ALPHABET)]


def _node_digest(node_id: str) -> str:
    return hashlib.blake2s(node_id.encode(), digest_size=5).hexdigest()


def _ifname(kind: str, node_id: str, index: int) -> str:
    name = f"{kind}{_base36_2(index)}-{_node_digest(node_id)}"
    if len(name) > LINUX_IFNAME_MAX:
        raise ValueError(f"internal interface-name budget error for {name!r}")
    return name


def gs_bridge_port_name(gs_id: str, index: int = 0) -> str:
    """Host-side veth name for a ground-station terminal bridge port."""
    return _ifname("g", gs_id, index)


def satellite_ground_host_name(sat_id: str, index: int = 0) -> str:
    """Host-side veth name for a satellite ground terminal."""
    return _ifname("s", sat_id, index)


def isl_host_name(node_id: str, index: int) -> str:
    """Host-side veth name for one satellite ISL terminal."""
    return _ifname("i", node_id, index)


# The link group (IFLA_GROUP) every host-side device NodalArc creates carries.
# It is the only mark of ownership: host cleanup deletes the group's members
# and nothing else, whatever other devices are named.
MANAGED_HOST_DEVICE_GROUP = 0x4E415243


class VxlanHostNames(NamedTuple):
    """Host-side device names of one VXLAN-carried link."""

    tunnel: str
    host_veth: str
    pod_veth: str


def vxlan_host_ifnames(vni: int) -> VxlanHostNames:
    """The host-side names of one VXLAN-carried link, distinct for every VNI."""
    if isinstance(vni, bool) or not isinstance(vni, int) or not VNI_MIN <= vni <= VNI_MAX:
        raise ValueError(f"VNI out of range {VNI_MIN}..{VNI_MAX}: {vni!r}")
    tag = f"{vni:06x}"
    return VxlanHostNames(tunnel=f"vx{tag}", host_veth=f"vh{tag}", pod_veth=f"vp{tag}")


# Every site-LAN bridge name starts with this prefix; the host firewall rule
# for site-LAN transit matches it.
SITE_LAN_BRIDGE_PREFIX = "sl"


def site_lan_bridge_name(vni: int) -> str:
    """Host bridge carrying one site's LAN segment on this host."""
    return f"{SITE_LAN_BRIDGE_PREFIX}{vni:08d}"


def site_lan_vxlan_name(vni: int) -> str:
    """Host VXLAN port joining this host's site-LAN bridge to peer hosts."""
    return f"sv{vni:08d}"


def site_lan_member_host_ifname(vni: int, member_index: int) -> str:
    """Host-side veth end (bridge port) for one site member pod."""
    if not 0 <= member_index <= 99:
        raise ValueError(f"site LAN member index out of range: {member_index}")
    return f"sm{vni:08d}{member_index:02d}"


def site_lan_member_pod_ifname(vni: int, member_index: int) -> str:
    """Transit name for the pod-side veth end before it becomes terr0."""
    if not 0 <= member_index <= 99:
        raise ValueError(f"site LAN member index out of range: {member_index}")
    return f"sp{vni:08d}{member_index:02d}"
