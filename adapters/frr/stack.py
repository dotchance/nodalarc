# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR's routing stack for one router.

The stack is the FRR adapter's selection for a router: which FRR daemons run
and which configuration fragments assemble into ``frr.conf``, in what order.
It is the union of what every routing domain the router participates in
needs, plus OSPFv3 beside OSPFv2 when the router carries IPv6.

Kernel requirements (MPLS labels, TTL propagation) are engine-neutral
substrate facts and live in ``nodalarc.substrate.routing_requirements``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nodalarc.workloads.adapter import AdapterRenderRefusal

from adapters.frr.support import FRR_SRGB, FRR_SUPPORT

if TYPE_CHECKING:
    from nodalarc.model_validation import AddressFamily
    from nodalarc.models.resolved_session import ResolvedRoutingDomain

# The IGP daemon FRR runs for each link-state protocol it renders.
_IGP_DAEMONS = {"isis": "isisd", "ospf": "ospfd"}
# The Segment Routing Local Block a router advertises.
SRLB = (40000, 49999)
# Every FRR daemon logs to one file in the integrated configuration.
LOG_FILE = "/var/log/frr/frr.log"


@dataclass(frozen=True)
class RouterStack:
    """FRR's selection for one router.

    ``daemons`` are the FRR daemons the router runs and ``fragments`` the
    configuration templates that assemble into ``frr.conf``, both in
    assembly order.
    """

    daemons: tuple[str, ...]
    fragments: tuple[str, ...]


def domain_supported(domain: ResolvedRoutingDomain) -> None:
    """Refuse a domain outside the FRR support declaration.

    The resolver refuses such a session before any rendering, so reaching
    here with one is a contract violation.
    """
    protocol_support = FRR_SUPPORT.routing.get(domain.protocol)
    if protocol_support is None:
        raise AdapterRenderRefusal(
            f"routing domain {domain.domain_id!r} uses protocol {domain.protocol!r}, "
            "which the FRR adapter does not render"
        )
    unrendered = sorted(set(domain.capabilities) - protocol_support.capabilities)
    if unrendered:
        raise AdapterRenderRefusal(
            f"routing domain {domain.domain_id!r} declares capabilities {unrendered} "
            f"that the FRR adapter does not render for {domain.protocol!r}"
        )
    if domain.timers.bfd.enabled and protocol_support.bfd is None:
        raise AdapterRenderRefusal(
            f"routing domain {domain.domain_id!r} enables BFD, which the FRR adapter does "
            f"not render for {domain.protocol!r}"
        )


def resolve_router_stack(
    domains: tuple[ResolvedRoutingDomain, ...],
    address_families: frozenset[AddressFamily],
) -> RouterStack:
    """Select the daemons and fragments for a router in ``domains``."""
    if not domains:
        raise AdapterRenderRefusal("an FRR router participates in at least one routing domain")
    for domain in domains:
        domain_supported(domain)
    protocols = {domain.protocol for domain in domains}
    for protocol in sorted(protocols):
        limit = FRR_SUPPORT.routing[protocol].domains_per_router
        shared = [domain.domain_id for domain in domains if domain.protocol == protocol]
        if limit is not None and len(shared) > limit:
            raise AdapterRenderRefusal(
                f"an FRR router renders {limit} {protocol} domain(s); this router "
                f"participates in {shared}"
            )
    capabilities = {capability for domain in domains for capability in domain.capabilities}
    # mgmtd loads the integrated configuration in FRR 10; zebra owns
    # interfaces and the RIB. Both run on every FRR router.
    selected = ["mgmtd", "zebra"]
    fragments = ["global", "zebra"]
    if any(domain.timers.bfd.enabled for domain in domains):
        # The BFD profiles precede the IGP interfaces that reference them.
        selected.append("bfdd")
        fragments.append("bfdd")
    for protocol in ("isis", "ospf"):
        if protocol in protocols:
            selected.append(_IGP_DAEMONS[protocol])
            fragments.append(_IGP_DAEMONS[protocol])
    if "ospf" in protocols and "ipv6" in address_families:
        # OSPFv2 routes IPv4 only; an IPv6 router also runs OSPFv3.
        selected.append("ospf6d")
        fragments.append("ospf6d")
    if "segment_routing" in capabilities:
        selected.append("pathd")
        fragments.append("pathd")
    if any(
        "mpls" in domain.capabilities and "segment_routing" not in domain.capabilities
        for domain in domains
    ):
        # A bare mpls capability means LDP-distributed labels; SR-MPLS
        # provides its own labels and runs no LDP.
        selected.append("ldpd")
        fragments.append("ldpd")
    selected.append("staticd")
    fragments.append("staticd")
    return RouterStack(daemons=tuple(selected), fragments=tuple(fragments))


def validate_sid_indices(
    domains: tuple[ResolvedRoutingDomain, ...],
    sid_by_domain: Mapping[str, Mapping[str, int]],
) -> None:
    """Validate resolver-owned prefix-SID indices against the router SRGB.

    The resolver owns SID allocation. FRR owns only the SRGB size. No caller
    derives SID indices from plane/slot, node kind, or ground-station order.
    """
    srgb_size = FRR_SRGB[1] - FRR_SRGB[0] + 1
    for domain in domains:
        if "segment_routing" not in domain.capabilities:
            continue
        indices = sid_by_domain.get(domain.domain_id)
        if not indices:
            raise AdapterRenderRefusal(
                f"segment routing domain {domain.domain_id!r} requires resolved SID indices"
            )
        invalid = {node_id: sid for node_id, sid in indices.items() if sid <= 0 or sid > srgb_size}
        if invalid:
            examples = ", ".join(
                f"{node_id}={sid}" for node_id, sid in sorted(invalid.items())[:10]
            )
            raise AdapterRenderRefusal(
                f"resolved SID index exceeds SRGB size {srgb_size}: {examples}"
            )
