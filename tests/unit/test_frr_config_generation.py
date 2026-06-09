# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR template rendering from catalog-resolved runtime truth."""

from __future__ import annotations

import re
from typing import Any

import nodalarc.template_vars as template_vars
import pytest
from jinja2 import Environment, FileSystemLoader
from nodalarc.models.resolved_session import ResolvedSession, SourceContext
from nodalarc.resolve_session import resolve_session
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars_from_resolved

from tests.conftest import CONFIGS_DIR, build_segment_session_dict

TEMPLATES_DIR = CONFIGS_DIR / "templates" / "frr"


def test_legacy_session_config_template_builder_is_removed() -> None:
    assert not hasattr(template_vars, "build_template_vars")


def _raw_session(
    *,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    planes: int = 2,
    slots: int = 2,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_segment_session_dict(
        name=f"test-{protocol}",
        constellation={"planes": {"count": planes, "sats_per_plane": slots}},
        ground_stations={"stations": [{} for _ in range(2)]},
        protocol=protocol,
        extensions=extensions or [],
        routing=routing,
    )


def _resolved(
    *,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    planes: int = 2,
    slots: int = 2,
    routing: dict[str, Any] | None = None,
) -> ResolvedSession:
    return resolve_session(
        _raw_session(
            protocol=protocol,
            extensions=extensions,
            planes=planes,
            slots=slots,
            routing=routing,
        ),
        source_context=SourceContext(origin="test.frr", run_id="run-test-0001"),
    )


def _render(template_name: str, vars_for_node: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
    )
    return env.get_template(template_name).render(**vars_for_node)


def _domain_for_node(resolved: ResolvedSession, node_id: str):
    domains = [domain for domain in resolved.routing_domains if node_id in domain.node_ids]
    assert len(domains) == 1
    return domains[0]


def _extensions_for_domain(domain) -> list[str]:
    capabilities = set(domain.capabilities)
    extensions: list[str] = []
    if "segment_routing" in capabilities:
        extensions.append("sr")
    if "traffic_engineering" in capabilities:
        extensions.append("te")
    if "mpls" in capabilities and "traffic_engineering" in capabilities:
        extensions.append("mpls")
    return extensions


def _vars_for(resolved: ResolvedSession, node_id: str) -> dict[str, Any]:
    domain = _domain_for_node(resolved, node_id)
    stack = resolve_stack(domain.protocol, _extensions_for_domain(domain))
    ground_ids = [node.node_id for node in resolved.nodes if node.kind == "ground_station"]
    return build_template_vars_from_resolved(
        resolved,
        node_id,
        stack_variables=stack.template_variables,
        node_sid_index=resolved.sid_index_by_node_id().get(node_id)
        if stack.segment_routing
        else None,
        gs_index=ground_ids.index(node_id) if node_id in ground_ids else None,
    )


def _first_satellite(resolved: ResolvedSession) -> str:
    return next(node.node_id for node in resolved.nodes if node.kind == "satellite")


def _first_ground(resolved: ResolvedSession) -> str:
    return next(node.node_id for node in resolved.nodes if node.kind == "ground_station")


def test_isis_satellite_uses_resolved_loopback_unnumbered_wan_and_sid() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr"])
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    zebra = _render("zebra.conf.j2", vars_for_node)
    isis = _render("isisd.conf.j2", vars_for_node)

    assert f"hostname {node_id}" in zebra
    assert f"ip address {vars_for_node['ipv4_loopback']}/32" in zebra
    assert "interface isl0" in zebra
    assert "interface gnd0" in zebra
    assert "router isis NODAL" in isis
    assert "isis network point-to-point" in isis
    assert "segment-routing on" in isis
    sid = int(re.search(r"index\s+(\d+)", isis).group(1))
    assert 0 < sid <= 8000


def test_isis_ground_renders_numbered_terr0_and_unnumbered_term_interfaces() -> None:
    resolved = _resolved(protocol="isis")
    node_id = _first_ground(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    zebra = _render("zebra.conf.j2", vars_for_node)
    isis = _render("isisd.conf.j2", vars_for_node)

    assert "interface terr0" in zebra
    assert "ip address 172.16.0.1/24" in zebra
    assert "interface term0" in zebra
    assert f"ip address {vars_for_node['ipv4_loopback']}/32" in zebra
    assert "interface terr0" in isis
    assert "isis passive" in isis


def test_default_route_is_originated_from_prefix_intent_not_as_an_interface_address() -> None:
    raw = _raw_session(protocol="isis")
    raw["segments"][1]["apply"]["originated_prefixes"] = {"ipv4": ["0.0.0.0/0"]}
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr"))
    vars_for_node = _vars_for(resolved, _first_ground(resolved))

    zebra = _render("zebra.conf.j2", vars_for_node)
    isis = _render("isisd.conf.j2", vars_for_node)

    assert "0.0.0.0/0" not in zebra
    assert "default-information originate ipv4" in isis


def test_ospf_satellite_uses_resolved_point_to_point_links_and_te() -> None:
    resolved = _resolved(protocol="ospf", extensions=["te", "mpls"])
    vars_for_node = _vars_for(resolved, _first_satellite(resolved))

    ospf = _render("ospfd.conf.j2", vars_for_node)

    assert "router ospf" in ospf
    assert "mpls-te on" in ospf
    assert "ip ospf network point-to-point" in ospf
    assert "ip ospf cost" in ospf


def test_ospf_cross_area_link_uses_backbone_area_from_resolved_area_assignment() -> None:
    raw = _raw_session(protocol="ospf", planes=2, slots=1)
    raw["link_rules"][1]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "sat-p00s00", "b": "sat-p01s00"}],
    }
    raw["routing"]["domains"][0]["area_assignment"] = {"strategy": "per_plane"}
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr"))
    vars_for_node = _vars_for(resolved, "space-sat-p00s00")

    assert any(info["cross_area"] for info in vars_for_node["interface_info"].values())
    ospf = _render("ospfd.conf.j2", vars_for_node)
    assert "ip ospf area 0.0.0.0" in ospf


def test_resolved_template_vars_fail_loud_when_sr_sid_is_missing() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr"])
    node_id = _first_satellite(resolved)
    stack = resolve_stack("isis", ["sr"])

    with pytest.raises(ValueError, match="SID index"):
        build_template_vars_from_resolved(
            resolved,
            node_id,
            stack_variables=stack.template_variables,
        )
