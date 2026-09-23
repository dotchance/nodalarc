# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel requirements a routing domain places on its members."""

from __future__ import annotations

import pytest
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.substrate.routing_requirements import routing_kernel_requirements

_SR_SYSCTLS = {"net.mpls.platform_labels": "100000", "net.mpls.ip_ttl_propagate": "0"}
_LDP_SYSCTLS = {"net.mpls.platform_labels": "100000"}


@pytest.mark.parametrize(
    ("protocol", "capabilities", "sysctls", "segment_routing"),
    [
        ("isis", (), {}, False),
        ("isis", ("traffic_engineering",), {}, False),
        ("isis", ("mpls",), _LDP_SYSCTLS, False),
        ("isis", ("mpls", "traffic_engineering"), _LDP_SYSCTLS, False),
        ("isis", ("segment_routing",), _SR_SYSCTLS, True),
        ("isis", ("mpls", "segment_routing"), _SR_SYSCTLS, True),
        ("ospf", ("mpls",), _LDP_SYSCTLS, False),
        ("ospf", ("segment_routing", "traffic_engineering"), _SR_SYSCTLS, True),
        ("static", (), {}, False),
    ],
)
def test_kernel_requirements_follow_the_domain_data_plane(
    protocol, capabilities, sysctls, segment_routing
) -> None:
    requirements = routing_kernel_requirements(
        ResolvedRoutingDomain(
            domain_id="d1", protocol=protocol, node_ids=("n1",), capabilities=capabilities
        )
    )

    assert requirements.sysctls == sysctls
    assert requirements.mpls_enable is bool(sysctls)
    assert requirements.segment_routing is segment_routing
