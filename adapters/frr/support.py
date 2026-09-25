# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The FRR adapter's declaration: its name and what it renders.

This module is data only. Runtime support reads it in every service that
resolves sessions, including images that do not install the rendering
dependencies, so it imports nothing beyond the core contract types.
"""

from __future__ import annotations

from nodalarc.models.segment_session import RoutingCapability
from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

# The adapter's name: the value a profile's `adapter:` field carries, and the
# key the explicit registry uses.
FRR_ADAPTER_NAME = "frr"

# FRR 10.3 accepts a detect multiplier of 1..255 and receive and transmit
# intervals of 10..4294967 milliseconds (bfdd's CLI ranges). isisd starts BFD
# on a circuit that routes IPv6 only for an adjacency with an IPv6 link-local
# address (isis_bfd.c, bfd_handle_adj_up), so IS-IS BFD never starts toward an
# IPv4-only peer on an interface that also reaches IPv6 peers. OSPFv2 and
# OSPFv3 keep one BFD peer per family.
FRR_ISIS_BFD_SUPPORT = BfdSupport(
    detect_multiplier=(1, 255),
    rx_interval_ms=(10, 4294967),
    tx_interval_ms=(10, 4294967),
)
FRR_OSPF_BFD_SUPPORT = BfdSupport(
    detect_multiplier=(1, 255),
    rx_interval_ms=(10, 4294967),
    tx_interval_ms=(10, 4294967),
    mixed_family_peers=True,
)

# Reference bandwidth for fixed-link IGP metrics: the metric of a fixed link
# is this value over the transmit rate of the interface's own terminal,
# truncated to an integer and never below 1, so every terminal at or above
# 100 Gb/s costs 1. Each end of a link sends at its own rate, so the two ends
# of an asymmetric link carry different metrics.
FRR_REFERENCE_BANDWIDTH_MBPS = 100_000
# The largest interface metric each IGP accepts: the OSPF cost, and the IS-IS
# wide metric below 2^24 - 1, which removes a link from SPF (RFC 5305).
FRR_MAXIMUM_IGP_METRIC = {"isis": 16_777_214, "ospf": 65_535}


def _link_rate_floor(protocol: str) -> float:
    """The transmit rate at or below which a fixed link's metric exceeds the
    protocol's maximum: the metric reaches maximum + 1 exactly at it."""
    return FRR_REFERENCE_BANDWIDTH_MBPS / (FRR_MAXIMUM_IGP_METRIC[protocol] + 1)


# The Segment Routing Global Block a router advertises; it is router-wide,
# shared by every segment-routing domain the router participates in.
FRR_SRGB = (16000, 23999)
_SID_INDEX_CAPACITY = FRR_SRGB[1] - FRR_SRGB[0] + 1

# Both IGP fragments render LDP-distributed MPLS, SR-MPLS prefix SIDs and
# MPLS-TE router-level enablement, each for IPv4.
_IGP_CAPABILITIES: frozenset[RoutingCapability] = frozenset(
    {"mpls", "segment_routing", "traffic_engineering"}
)

# IS-IS routes IPv6 in its own topology (multi-topology), OSPF with OSPFv3
# beside OSPFv2, and static routes carry either family.
_BOTH_FAMILIES = frozenset({"ipv4", "ipv6"})

# A router's loopback belongs to every domain it participates in. FRR runs an
# interface in one IS-IS instance, and this adapter renders one OSPF instance
# per router, so a router carries at most one domain of each IGP. Static
# domains run no protocol instance and combine freely.
FRR_SUPPORT = AdapterSupport(
    routing={
        "isis": RoutingProtocolSupport(
            capabilities=_IGP_CAPABILITIES,
            bfd=FRR_ISIS_BFD_SUPPORT,
            address_families=_BOTH_FAMILIES,
            domains_per_router=1,
            link_rate_floor_mbps=_link_rate_floor("isis"),
            sid_index_capacity=_SID_INDEX_CAPACITY,
        ),
        "ospf": RoutingProtocolSupport(
            capabilities=_IGP_CAPABILITIES,
            bfd=FRR_OSPF_BFD_SUPPORT,
            address_families=_BOTH_FAMILIES,
            domains_per_router=1,
            link_rate_floor_mbps=_link_rate_floor("ospf"),
            sid_index_capacity=_SID_INDEX_CAPACITY,
        ),
        # Static routes carry no metric, so every link rate renders.
        "static": RoutingProtocolSupport(
            address_families=_BOTH_FAMILIES, domains_per_router=None, link_rate_floor_mbps=None
        ),
    }
)
