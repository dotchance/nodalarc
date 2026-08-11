# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Space carriers expand onboard buses and payload members behind the gate."""

from __future__ import annotations

import ipaddress

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.runtime_support import FeatureCategory, RuntimeSupport, UnsupportedFeatureError

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    resolve_catalog_session,
)

# Onboard execution is production-supported; a narrowed profile must still
# refuse typed — the boundary mechanism outlives any one profile's width.
NO_ONBOARD_SUPPORT = RuntimeSupport.earth_luna().model_copy(
    update={"supports_payloads": False}
)


def _fixture_with_vehicle():
    raw = build_catalog_session_fixture(
        name="space-payload-expansion",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": [{}]},
    )
    payload_ref = CatalogRef("user:payloads/space-dtn-host.yaml")
    raw.create_catalog(
        payload_ref,
        {
            "payload": {
                "id": payload_ref.relative_path.stem,
                "forwarding": "host",
                "profile": "nodalarc:profiles/linux-host.yaml",
            }
        },
    )
    node_document = raw.read_catalog(raw.space_node_ref)
    node = node_document["node"]
    node["ethernet"] = [{"id": "bus0"}, {"id": "bus1"}]
    node["payloads"] = [
        {
            "id": "dtn",
            "payload": str(payload_ref),
            "count": 1,
            "attach": "bus1",
            "tags": ["dtn_relay"],
        }
    ]
    node["originated_prefixes"] = {"ipv4": ["bus1"], "ipv6": ["bus1"]}
    raw.write_catalog(raw.space_node_ref, node_document)
    return raw


def test_space_onboard_execution_refuses_typed_under_a_narrowed_profile() -> None:
    raw = _fixture_with_vehicle()

    with pytest.raises(UnsupportedFeatureError) as err:
        resolve_catalog_session(raw, runtime_support=NO_ONBOARD_SUPPORT)

    assert any(
        feature.category == FeatureCategory.PAYLOAD
        and feature.value == "space_payload_execution"
        for feature in err.value.features
    )


def test_space_carrier_expands_buses_members_and_origination() -> None:
    raw = _fixture_with_vehicle()

    resolved = resolve_catalog_session(raw)

    carriers = [
        node
        for node in resolved.nodes
        if node.kind == "satellite" and node.forwarding == "routed"
    ]
    members = [
        node
        for node in resolved.nodes
        if node.kind == "satellite" and node.forwarding == "host"
    ]
    assert len(carriers) == 2
    assert len(members) == 2

    subnets_seen: set[str] = set()
    for carrier in carriers:
        member = next(
            node for node in members if node.node_id == f"{carrier.node_id}-dtn"
        )
        # Members ride the carrier: same segment, same motion, same grid.
        assert member.local_node_id == f"{carrier.local_node_id}-dtn"
        assert member.orbit == carrier.orbit
        assert member.plane == carrier.plane
        assert member.slot == carrier.slot
        assert "dtn_relay" in member.tags
        assert member.profile == "nodalarc:profiles/linux-host.yaml"
        assert member.terminal_inventory == ()

        # The carrier serves both buses; the member joins the mount's bus.
        assert set(carrier.interfaces.ethernet) == {"bus0", "bus1"}
        assert set(member.interfaces.ethernet) == {"bus1"}
        carrier_bus1 = ipaddress.ip_interface(carrier.interfaces.ethernet["bus1"].ipv4)
        member_bus1 = ipaddress.ip_interface(member.interfaces.ethernet["bus1"].ipv4)
        assert carrier_bus1.network == member_bus1.network
        assert carrier_bus1.ip != member_bus1.ip

        # Attachment facts point at the carrier as the bus gateway.
        attachment = member.host_attachment
        assert attachment is not None
        assert attachment.interface == "bus1"
        assert attachment.gateway_node_id == carrier.node_id
        assert attachment.gateway_ipv4 == str(carrier_bus1.ip)

        # Symbolic self-port origination resolved to the allocated subnet.
        assert carrier.originated_prefixes is not None
        assert carrier.originated_prefixes.ipv4 == (str(carrier_bus1.network),)

        # Each placed copy owns its own bus subnets.
        for address in carrier.interfaces.ethernet.values():
            subnet = str(ipaddress.ip_interface(address.ipv4).network)
            assert subnet not in subnets_seen
            subnets_seen.add(subnet)

    # Members join no routing domain; carriers all do.
    domain_members = {
        node_id for domain in resolved.routing_domains for node_id in domain.node_ids
    }
    assert all(carrier.node_id in domain_members for carrier in carriers)
    assert all(member.node_id not in domain_members for member in members)
