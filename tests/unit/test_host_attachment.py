# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Host-forwarding nodes: derived substrate attachment, no invented routing."""

from __future__ import annotations

import pytest
from nodalarc.resolve_session import SessionResolutionError, resolve_session_with_assets
from nodalarc_operator.workloads.preparation import prepare_session_workloads

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    resolve_catalog_session,
)


def _resolved_with_hosts(**overrides):
    fixture = build_catalog_session_fixture(
        name=overrides.pop("name", "host-attachment"),
        constellation={},
        ground_stations={"stations": [{}, {}], "host_endpoints": True},
        **overrides,
    )
    return resolve_catalog_session(fixture)


def test_host_nodes_resolve_with_derived_attachment() -> None:
    resolved = _resolved_with_hosts()
    hosts = [node for node in resolved.nodes if node.forwarding == "host"]
    routers = {node.node_id: node for node in resolved.nodes if node.forwarding == "routed"}
    assert len(hosts) == 2
    for host in hosts:
        attachment = host.host_attachment
        assert attachment is not None
        assert attachment.interface == "terr0"
        # The host's address is its authored terr0 placement.
        assert attachment.ipv4 == host.interfaces.terr0.ipv4
        # The gateway is the routed node sharing the site LAN, named by id,
        # and its address is that node's terr0 without the prefix.
        gateway = routers[attachment.gateway_node_id]
        assert gateway.interfaces.terr0.ipv4.split("/", 1)[0] == attachment.gateway_ipv4
        assert attachment.ipv4.split("/", 1)[1] == gateway.interfaces.terr0.ipv4.split("/", 1)[1]


def test_routed_nodes_carry_no_attachment() -> None:
    resolved = _resolved_with_hosts(name="host-attachment-none")
    for node in resolved.nodes:
        if node.forwarding != "host":
            assert node.host_attachment is None


def test_hosts_stay_out_of_the_default_routing_domain() -> None:
    resolved = _resolved_with_hosts(name="host-attachment-domain")
    host_ids = {node.node_id for node in resolved.nodes if node.forwarding == "host"}
    assert host_ids
    for domain in resolved.routing_domains:
        assert not host_ids.intersection(domain.node_ids)


def test_explicit_domains_are_defined_over_routed_nodes() -> None:
    """A set selector sweeping hosts takes its routed subset — the same
    universe as the documented no-routing default — and hosts never appear
    in any domain."""
    resolved = _resolved_with_hosts(
        name="host-attachment-explicit",
        routing={
            "domains": [
                {
                    "id": "everything",
                    "protocol": "isis",
                    "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                    "area_assignment": {"strategy": "flat"},
                }
            ]
        },
    )
    host_ids = {node.node_id for node in resolved.nodes if node.forwarding == "host"}
    assert host_ids
    for domain in resolved.routing_domains:
        assert not host_ids.intersection(domain.node_ids)


def test_domain_matching_only_hosts_is_refused() -> None:
    with pytest.raises(SessionResolutionError, match="contains zero routers"):
        _resolved_with_hosts(
            name="host-attachment-hosts-only",
            routing={
                "domains": [
                    {
                        "id": "endpoints",
                        "protocol": "isis",
                        "selectors": [{"tag": "test_host"}],
                    },
                    {
                        "id": "rest",
                        "protocol": "isis",
                        "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                        "area_assignment": {"strategy": "flat"},
                    },
                ]
            },
        )


def test_one_path_composes_hosts_and_routers_from_their_profiles() -> None:
    """Host nodes compose from the plain application profile — one
    zero-capability container, no rendered artifacts — while routed nodes
    compose FRR with rendered configuration, in ONE session under one sky."""
    fixture = build_catalog_session_fixture(
        name="host-hetero",
        constellation={},
        ground_stations={"stations": [{}, {}], "host_endpoints": True},
    )
    resolution = resolve_session_with_assets(fixture, catalog_roots=fixture.roots)
    prepared = prepare_session_workloads(
        resolution,
        namespace="nodalarc",
        owner_ref={"kind": "ConstellationSpec", "name": "s", "uid": "u1"},
    )

    hosts = {n.node_id for n in resolution.resolved.nodes if n.forwarding == "host"}
    assert hosts
    assert prepared.identity.startswith("profiles@sha256:")
    for node_id, composed in prepared.composed.items():
        names = [c.name for c in composed.composition.containers]
        if node_id in hosts:
            assert names == ["linux-host"], names
            assert composed.artifact_config_map is None
        else:
            assert names[0] == "frr-router", names
            assert composed.artifact_config_map is not None
