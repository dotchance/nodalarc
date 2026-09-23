# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel requirements a routing domain places on its members' namespaces.

These are substrate facts: any forwarding engine that runs a domain with an
MPLS data plane needs the same kernel label table, whoever computes the
labels. The Operator writes them into the wiring manifest and the Node Agent
applies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedRoutingDomain

# Kernel MPLS label table size for an MPLS data plane.
_MPLS_PLATFORM_LABELS = "100000"


@dataclass(frozen=True)
class RoutingKernelRequirements:
    """Per-node kernel settings that one routing domain requires.

    ``mpls_enable`` is true exactly when a ``net.mpls.*`` sysctl is required.
    ``segment_routing`` records that the domain's MPLS data plane is SR-MPLS.
    """

    sysctls: dict[str, str]
    mpls_enable: bool
    segment_routing: bool


def routing_kernel_requirements(domain: ResolvedRoutingDomain) -> RoutingKernelRequirements:
    """Kernel requirements for every member of ``domain``.

    SR-MPLS needs the label table and pipe-mode TTL: the MPLS TTL starts at
    255 whatever the IP TTL, so tracepath and traceroute work through MPLS
    tunnels (uniform mode copies the IP TTL, and a probe with TTL 1 expires
    at the first MPLS transit hop). An ``mpls`` data plane without segment
    routing needs the label table only.
    """
    capabilities = set(domain.capabilities)
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
        segment_routing=segment_routing,
    )
