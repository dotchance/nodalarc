# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""What the FRR adapter renders, as a support declaration.

This module is data only. Runtime support reads it in every service that
resolves sessions, including images that do not install the rendering
dependencies, so it imports nothing beyond the core contract types.
"""

from __future__ import annotations

from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

# FRR 10.3 accepts a detect multiplier of 1..255 and receive and transmit
# intervals of 10..4294967 milliseconds (bfdd's CLI ranges).
FRR_BFD_SUPPORT = BfdSupport(
    detect_multiplier=(1, 255),
    rx_interval_ms=(10, 4294967),
    tx_interval_ms=(10, 4294967),
)

# Both IGP fragments render LDP-distributed MPLS, SR-MPLS prefix SIDs and
# MPLS-TE router-level enablement, each for IPv4.
_IGP_CAPABILITIES = frozenset({"mpls", "segment_routing", "traffic_engineering"})

# IS-IS routes IPv6 in its own topology (multi-topology), OSPF with OSPFv3
# beside OSPFv2, and static routes carry either family.
_BOTH_FAMILIES = frozenset({"ipv4", "ipv6"})

FRR_SUPPORT = AdapterSupport(
    routing={
        "isis": RoutingProtocolSupport(
            capabilities=_IGP_CAPABILITIES, bfd=FRR_BFD_SUPPORT, address_families=_BOTH_FAMILIES
        ),
        "ospf": RoutingProtocolSupport(
            capabilities=_IGP_CAPABILITIES, bfd=FRR_BFD_SUPPORT, address_families=_BOTH_FAMILIES
        ),
        "static": RoutingProtocolSupport(address_families=_BOTH_FAMILIES),
    }
)
