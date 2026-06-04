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

K8S_LABEL_VALUE_MAX = 63
LINUX_IFNAME_MAX = 15
_RUNTIME_NODE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_CURRENT_HOST_IFNAME_RE = re.compile(r"^[isg][0-9a-z]{2}-[0-9a-f]{10}$")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_RETIRED_HOST_IFNAME_PREFIXES = ("_isl_", "_gnd_", "_gbr-", "_na_")


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


def is_managed_host_ifname(name: str) -> bool:
    """Return True for host-side interfaces owned by NodalArc.

    Cleanup code must recognize both the current resolver-owned bounded names
    and retired names from older deployments, because a reused node can contain
    either after a restart or session switch.
    """
    if _CURRENT_HOST_IFNAME_RE.fullmatch(name):
        return True
    if name.startswith("br-gnd-"):
        return True
    if any(name.startswith(prefix) for prefix in _RETIRED_HOST_IFNAME_PREFIXES):
        return True
    # Retired ground bridge port shape, e.g. _g0...; keep this here so cleanup
    # callers do not re-encode old naming knowledge.
    return name.startswith("_g") and len(name) > 2 and name[2:3].isdigit()
