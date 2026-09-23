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
# MPLS-TE router-level enablement.
_IGP_CAPABILITIES = frozenset({"mpls", "segment_routing", "traffic_engineering"})

FRR_SUPPORT = AdapterSupport(
    routing={
        "isis": RoutingProtocolSupport(capabilities=_IGP_CAPABILITIES, bfd=FRR_BFD_SUPPORT),
        "ospf": RoutingProtocolSupport(capabilities=_IGP_CAPABILITIES, bfd=FRR_BFD_SUPPORT),
        "static": RoutingProtocolSupport(),
    }
)
