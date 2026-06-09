# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR template rendering from catalog-resolved runtime truth."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import nodalarc.template_vars as template_vars
import pytest
from jinja2 import Environment, FileSystemLoader
from nodalarc.models.resolved_session import ResolvedSession, SourceContext
from nodalarc.resolve_session import load_session_resolution_from_file, resolve_session
from nodalarc.stack_resolver import domain_extensions, resolve_domain_stack, resolve_stack
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


def _vars_for(resolved: ResolvedSession, node_id: str) -> dict[str, Any]:
    domain = _domain_for_node(resolved, node_id)
    stack = resolve_stack(domain.protocol, domain_extensions(domain))
    return build_template_vars_from_resolved(
        resolved,
        node_id,
        stack_variables=stack.template_variables,
        node_sid_index=resolved.sid_index_by_node_id().get(node_id)
        if stack.segment_routing
        else None,
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


# ---------------------------------------------------------------------------
# Identity and multi-domain rendering nets
# ---------------------------------------------------------------------------

NET_LINE = re.compile(
    r"^ net (?P<area>[0-9a-f.]+)\.(?P<system>[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\.00$"
)


def test_isis_system_ids_are_globally_unique_across_segments() -> None:
    """Four segments share plane/slot numbering in one IS-IS domain; every
    rendered NET must still be unique — identity comes from the resolver,
    never from per-segment indices."""
    resolution = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml")
    )
    resolved = resolution.resolved

    nets: dict[str, str] = {}
    for domain in resolved.routing_domains:
        stack = resolve_domain_stack(domain)
        sid_by_node = resolved.sid_index_by_node_id()
        for node_id in domain.node_ids:
            vars_for_node = build_template_vars_from_resolved(
                resolved,
                node_id,
                stack_variables=stack.template_variables,
                node_sid_index=sid_by_node.get(node_id) if stack.segment_routing else None,
            )
            rendered = _render("isisd.conf.j2", vars_for_node)
            net_lines = [line for line in rendered.splitlines() if NET_LINE.match(line)]
            assert len(net_lines) >= 1, f"{node_id}: no parseable NET in rendered isisd.conf"
            net = net_lines[0]
            assert net not in nets, (
                f"duplicate IS-IS NET {net!r} rendered for {node_id} and {nets[net]}"
            )
            nets[net] = node_id
    # Every routed node rendered exactly one unique NET.
    routed = sum(len(domain.node_ids) for domain in resolved.routing_domains)
    assert len(nets) == routed


def test_two_protocol_session_renders_each_domain_with_its_own_stack() -> None:
    raw = _raw_session(
        routing={
            "domains": [
                {
                    "id": "space_igp",
                    "protocol": "isis",
                    "selectors": [{"segment": "space"}],
                },
                {
                    "id": "ground_igp",
                    "protocol": "ospf",
                    "selectors": [{"segment": "ground"}],
                },
            ]
        }
    )
    resolved = resolve_session(
        raw, source_context=SourceContext(origin="test.frr", run_id="run-test-0001")
    )
    assert {(d.domain_id, d.protocol) for d in resolved.routing_domains} == {
        ("space_igp", "isis"),
        ("ground_igp", "ospf"),
    }

    sat_id = _first_satellite(resolved)
    ground_id = _first_ground(resolved)
    sat_stack = resolve_domain_stack(_domain_for_node(resolved, sat_id))
    ground_stack = resolve_domain_stack(_domain_for_node(resolved, ground_id))
    assert "isisd" in sat_stack.daemons and "ospfd" not in sat_stack.daemons
    assert "ospfd" in ground_stack.daemons and "isisd" not in ground_stack.daemons

    sat_isis = _render("isisd.conf.j2", _vars_for(resolved, sat_id))
    ground_ospf = _render("ospfd.conf.j2", _vars_for(resolved, ground_id))
    assert "router isis NODAL" in sat_isis
    assert " ip router isis NODAL" in sat_isis
    assert "router ospf" in ground_ospf
    assert " ip ospf area" in ground_ospf


def test_static_domain_renders_zebra_and_staticd_only() -> None:
    raw = _raw_session(protocol="static")
    resolved = resolve_session(
        raw, source_context=SourceContext(origin="test.frr", run_id="run-test-0001")
    )
    sat_id = _first_satellite(resolved)
    stack = resolve_domain_stack(_domain_for_node(resolved, sat_id))
    assert stack.daemons == ["zebra", "staticd"]

    zebra = _render("zebra.conf.j2", _vars_for(resolved, sat_id))
    staticd = _render("staticd.conf.j2", _vars_for(resolved, sat_id))
    # No IGP statements leak into a static-domain zebra config.
    assert "isis" not in zebra
    assert f"hostname {sat_id}" in zebra
    assert staticd.strip()


def test_boundary_exports_materialize_on_flagship_border_nodes() -> None:
    """The cislunar session's declared purpose is Earth<->Luna reachability:
    boundary exports must render as installable static routes plus IGP
    redistribution on the border nodes — declared intent, materialized."""
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml")
    ).resolved

    boundary = resolved.routing.boundaries[0]
    luna_domain = next(d for d in resolved.routing_domains if d.domain_id == "luna_domain")
    earth_domain = next(d for d in resolved.routing_domains if d.domain_id == "earth_domain")
    border_candidates = [c for c in resolved.link_candidates if c.rule_id == boundary.over]
    assert border_candidates, "boundary rule resolves zero candidates"
    luna_border = next(
        node_id
        for c in border_candidates
        for node_id in (c.node_a, c.node_b)
        if node_id in luna_domain.node_ids
    )

    vars_for_node = _vars_for(resolved, luna_border)
    routes = vars_for_node["boundary_static_routes"]
    assert routes, "luna border node materialized zero boundary routes"
    route_prefixes = {r["prefix"] for r in routes}

    # Every earth-domain routed loopback is exported (export_node_loopbacks).
    earth_loopbacks = {
        f"{node.interfaces.lo0.ipv4.split('/')[0]}/32"
        for node in resolved.nodes
        if node.node_id in earth_domain.node_ids
        and node.forwarding == "routed"
        and node.interfaces is not None
        and node.interfaces.lo0.ipv4 is not None
    }
    peer_seeds = {
        f"{_vars_for(resolved, luna_border)['interface_info'][iface]['peer_loopback_ipv4']}/32"
        for iface, info in vars_for_node["interface_info"].items()
        if info.get("static_only")
    }
    assert earth_loopbacks - peer_seeds <= route_prefixes

    # Earth-domain originated v4 aggregates are exported too.
    earth_originated = {
        prefix
        for node in resolved.nodes
        if node.node_id in earth_domain.node_ids and node.originated_prefixes is not None
        for prefix in node.originated_prefixes.ipv4 or ()
    }
    assert earth_originated
    assert earth_originated <= route_prefixes

    staticd = _render("staticd.conf.j2", vars_for_node)
    sample = sorted(route_prefixes)[0]
    assert f"ip route {sample} " in staticd

    isis = _render("isisd.conf.j2", vars_for_node)
    assert "redistribute ipv4 static level-2" in isis


def test_non_border_nodes_render_no_boundary_routes_or_redistribution() -> None:
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml")
    ).resolved
    vars_for_node = _vars_for(resolved, _first_satellite(resolved))
    assert vars_for_node["boundary_static_routes"] == []
    assert vars_for_node["redistribute_static"] is False
    isis = _render("isisd.conf.j2", vars_for_node)
    assert "redistribute" not in isis
