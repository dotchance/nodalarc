# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel requirements a node's session facts place on its namespace.

These are substrate facts: any forwarding engine that runs a domain with an
MPLS data plane needs the same kernel label table, whoever computes the
labels, and whether a node forwards a family follows from its role and the
address families the session gives it, whatever engine it runs. The
Operator writes them into the wiring manifest and the Node Agent applies
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedNode, ResolvedRoutingDomain

# Kernel MPLS label table size for an MPLS data plane.
_MPLS_PLATFORM_LABELS = "100000"


@dataclass(frozen=True)
class RoutingKernelRequirements:
    """Per-node kernel settings that one routing domain requires.

    ``mpls_enable`` is true exactly when a ``net.mpls.*`` sysctl is required.
    """

    sysctls: dict[str, str]
    mpls_enable: bool


def routing_kernel_requirements(
    domains: tuple[ResolvedRoutingDomain, ...],
) -> RoutingKernelRequirements:
    """Kernel requirements of a node that participates in ``domains``.

    The node needs what any of its domains needs; a node that participates
    in none needs nothing.

    SR-MPLS needs the label table and pipe-mode TTL: the MPLS TTL starts at
    255 whatever the IP TTL, so tracepath and traceroute work through MPLS
    tunnels (uniform mode copies the IP TTL, and a probe with TTL 1 expires
    at the first MPLS transit hop). An ``mpls`` data plane without segment
    routing needs the label table only.
    """
    capabilities = {capability for domain in domains for capability in domain.capabilities}
    segment_routing = "segment_routing" in capabilities
    sysctls: dict[str, str] = {}
    if segment_routing:
        sysctls = {
            "net.mpls.platform_labels": _MPLS_PLATFORM_LABELS,
            "net.mpls.ip_ttl_propagate": "0",
        }
    elif "mpls" in capabilities:
        sysctls = {"net.mpls.platform_labels": _MPLS_PLATFORM_LABELS}
    return RoutingKernelRequirements(
        sysctls=sysctls,
        mpls_enable=any(name.startswith("net.mpls.") for name in sysctls),
    )


def address_family_sysctls(node: ResolvedNode) -> dict[str, str]:
    """Kernel settings for the address families the session gives one node.

    Forwarding is stated for both families on every node: a routed node
    forwards each family it carries and no other, and a host forwards
    nothing. An IPv6 node skips duplicate address detection, so the
    addresses the substrate and the routing engine assign are usable at
    once; a node without IPv6 keeps the kernel's own setting.
    """
    families = node.address_families
    routed = node.forwarding == "routed"
    sysctls = {
        "net.ipv4.ip_forward": "1" if routed and "ipv4" in families else "0",
        "net.ipv6.conf.all.forwarding": "1" if routed and "ipv6" in families else "0",
    }
    if "ipv6" in families:
        sysctls["net.ipv6.conf.all.dad_transmits"] = "0"
        sysctls["net.ipv6.conf.default.dad_transmits"] = "0"
    return sysctls
