# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel requirements a node's routing domain and address families place on it."""

from __future__ import annotations

from pathlib import Path

import pytest
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.substrate.routing_requirements import (
    address_family_sysctls,
    routing_kernel_requirements,
)

from tests.catalog_session_fixtures import shipped_read_view

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


def _shipped(name: str):
    return load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions") / name, catalog=shipped_read_view()
    ).resolved


def test_a_router_forwards_exactly_the_families_the_session_gives_it() -> None:
    resolved = _shipped("earth-leo-simple.yaml")
    satellite = next(node for node in resolved.nodes if node.kind == "satellite")
    site_router = next(node for node in resolved.nodes if node.kind == "ground_station")
    assert satellite.address_families == {"ipv4"}
    assert site_router.address_families == {"ipv4", "ipv6"}

    # An IPv4-only router: IPv4 forwarding on, IPv6 forwarding off, and the
    # kernel's own IPv6 address handling untouched.
    assert address_family_sysctls(satellite) == {
        "net.ipv4.ip_forward": "1",
        "net.ipv6.conf.all.forwarding": "0",
    }
    # A router on a declared IPv6 LAN forwards both families and skips DAD.
    assert address_family_sysctls(site_router) == {
        "net.ipv4.ip_forward": "1",
        "net.ipv6.conf.all.forwarding": "1",
        "net.ipv6.conf.all.dad_transmits": "0",
        "net.ipv6.conf.default.dad_transmits": "0",
    }


def test_a_host_forwards_nothing_whatever_families_it_carries() -> None:
    resolved = _shipped("earth-luna-quic.yaml")
    hosts = [node for node in resolved.nodes if node.forwarding == "host"]
    assert hosts
    for host in hosts:
        sysctls = address_family_sysctls(host)
        assert sysctls["net.ipv4.ip_forward"] == "0"
        assert sysctls["net.ipv6.conf.all.forwarding"] == "0"
        assert ("net.ipv6.conf.all.dad_transmits" in sysctls) is ("ipv6" in host.address_families)
