# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""IPv6 exists exactly where the session and its catalog objects declare it."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import nodalarc.runtime_support as runtime_support
import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.resolve_session import SessionResolutionError, load_session_resolution_from_file
from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError
from nodalarc.workloads.adapter import AdapterSupport, RoutingProtocolSupport, SessionContext

from adapters.frr import FrrAdapter
from adapters.frr.support import FRR_SUPPORT
from tests.catalog_session_fixtures import (
    CatalogSessionFixture,
    build_catalog_session_fixture,
    shipped_read_view,
)
from tests.catalog_session_fixtures import resolve_catalog_session as resolve_session

SESSIONS = Path("catalog/nodalarc/sessions")


def _fixture(*, host_endpoints: bool = False) -> CatalogSessionFixture:
    return build_catalog_session_fixture(
        name="declared-families",
        constellation={"planes": {"count": 2, "sats_per_plane": 2}},
        ground_stations={"stations": [{}], "host_endpoints": host_endpoints},
    )


def _site_origination(fixture: CatalogSessionFixture, originated: dict) -> None:
    for ref in fixture.site_refs:
        document = fixture.read_catalog(ref)
        for node in document["site"]["nodes"]:
            node["originated_prefixes"] = originated
        fixture.write_catalog(ref, document)


def _space_loopbacks(fixture: CatalogSessionFixture, *, keep: str) -> None:
    fixture["addressing"]["loopbacks"] = [
        assignment
        for assignment in fixture["addressing"]["loopbacks"]
        if f"{keep}_pool" in assignment
    ]


def _frr_conf(resolved: ResolvedSession, node_id: str) -> str:
    node = resolved.node_by_id(node_id)
    assert node is not None
    rendered = FrrAdapter().render_node(node, SessionContext(resolved))
    return rendered.files["frr.conf"].decode()


def test_shipped_sessions_carry_ipv6_only_where_declared() -> None:
    for path in sorted(SESSIONS.glob("*.yaml")):
        raw = load_configuration_yaml(path.read_text(encoding="utf-8"))
        declares_ipv6_loopbacks = any(
            assignment.get("ipv6_pool")
            for assignment in (raw.get("addressing") or {}).get("loopbacks") or ()
        )
        resolved = load_session_resolution_from_file(path, catalog=shipped_read_view()).resolved
        nodes = {node.node_id: node for node in resolved.nodes}

        if not declares_ipv6_loopbacks:
            assert all(
                node.interfaces is None or node.interfaces.lo0.ipv6 is None
                for node in resolved.nodes
            ), path.name
        # A segment is IPv6 only because a member originates it into IPv6.
        for segment in resolved.ethernet_segments:
            if segment.ipv6_subnet is None:
                continue
            assert any(
                segment.ipv6_subnet in (nodes[member.node_id].originated_prefixes.ipv6 or ())
                for member in segment.members
                if nodes[member.node_id].originated_prefixes is not None
            ), (path.name, segment.scope_id, segment.segment_id)


def test_an_ipv4_only_lan_gives_its_members_and_hosts_no_ipv6() -> None:
    fixture = _fixture(host_endpoints=True)
    _site_origination(fixture, {"ipv4": ["lan0"]})
    _space_loopbacks(fixture, keep="ipv4")

    resolved = resolve_session(fixture)

    [segment] = resolved.ethernet_segments
    assert segment.ipv6_subnet is None
    for member in segment.members:
        node = resolved.node_by_id(member.node_id)
        assert node is not None and node.interfaces is not None
        assert node.interfaces.ethernet[member.interface].ipv6 is None
        assert node.address_families == {"ipv4"}
        if node.host_attachment is not None:
            assert node.host_attachment.ipv6 is None
            assert node.host_attachment.gateway_ipv6 is None
    router = next(node for node in resolved.nodes if node.kind == "ground_station")
    conf = _frr_conf(resolved, router.node_id)
    assert "ipv6" not in conf
    assert "topology ipv6-unicast" not in conf


def test_a_host_on_an_ipv6_lan_attaches_through_one_gateway_in_both_families() -> None:
    resolved = resolve_session(_fixture(host_endpoints=True))

    hosts = [node for node in resolved.nodes if node.forwarding == "host"]
    assert hosts
    for host in hosts:
        attachment = host.host_attachment
        assert attachment is not None and attachment.ipv6 is not None
        gateway = resolved.node_by_id(attachment.gateway_node_id)
        assert gateway is not None and gateway.interfaces is not None
        gateway_lan = gateway.interfaces.ethernet[attachment.interface]
        assert attachment.gateway_ipv4 == str(ipaddress.ip_interface(gateway_lan.ipv4).ip)
        assert attachment.gateway_ipv6 == str(ipaddress.ip_interface(gateway_lan.ipv6).ip)
        assert host.address_families == {"ipv4", "ipv6"}


def test_an_ipv6_only_loopback_assignment_keeps_the_resolver_ipv4_loopback() -> None:
    fixture = _fixture()
    _space_loopbacks(fixture, keep="ipv6")

    resolved = resolve_session(fixture)

    satellites = [node for node in resolved.nodes if node.kind == "satellite"]
    assert satellites
    for satellite in satellites:
        assert satellite.interfaces is not None
        lo0 = satellite.interfaces.lo0
        assert ipaddress.ip_interface(lo0.ipv6).ip in ipaddress.ip_network("fd00::/64")
        assert ipaddress.ip_interface(lo0.ipv4).ip in ipaddress.ip_network("100.64.0.0/10")
    loopbacks = [node.interfaces.lo0.ipv4 for node in resolved.nodes if node.interfaces]
    assert len(set(loopbacks)) == len(loopbacks)


def test_a_space_node_originating_an_ipv6_default_routes_ipv6_without_segments() -> None:
    fixture = _fixture()
    _space_loopbacks(fixture, keep="ipv4")
    node_document = fixture.read_catalog(fixture.space_node_ref)
    node_document["node"]["originated_prefixes"] = {"ipv6": ["default"]}
    fixture.write_catalog(fixture.space_node_ref, node_document)

    resolved = resolve_session(fixture)

    satellite = next(node for node in resolved.nodes if node.kind == "satellite")
    assert satellite.originated_prefixes is not None
    assert satellite.originated_prefixes.ipv6 == ("::/0",)
    assert satellite.address_families == {"ipv4", "ipv6"}
    conf = _frr_conf(resolved, satellite.node_id)
    assert " topology ipv6-unicast" in conf
    assert " default-information originate ipv6 level-2 always metric 100" in conf


def test_originating_a_segment_a_node_does_not_join_is_refused() -> None:
    fixture = _fixture()
    ground = next(segment for segment in fixture["segments"] if segment["id"] == "ground")
    ground["apply"] = {**(ground.get("apply") or {}), "originated_prefixes": {"ipv6": ["lan9"]}}

    with pytest.raises(SessionResolutionError, match="originates segment 'lan9', but is not"):
        resolve_session(fixture)


def test_a_router_whose_adapter_cannot_route_its_ipv6_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isis = FRR_SUPPORT.routing["isis"]
    ipv4_only = AdapterSupport(
        routing={
            **FRR_SUPPORT.routing,
            "isis": RoutingProtocolSupport(
                isis.capabilities, isis.bfd, address_families=frozenset({"ipv4"})
            ),
        }
    )
    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: {"frr": ipv4_only})

    with pytest.raises(UnsupportedFeatureError) as refused:
        load_session_resolution_from_file(
            SESSIONS / "earth-leo-simple.yaml", catalog=shipped_read_view()
        )

    [feature] = refused.value.features
    assert feature.category == FeatureCategory.ROUTING_ADDRESS_FAMILY
    assert feature.value == "isis:ipv6"
    # The site routers carry IPv6; the IPv4-only satellites are not named.
    assert "earth-us-co-denver-gw1" in feature.message
    assert "leo-" not in feature.message
