# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR configuration rendered from catalog-resolved runtime truth."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from nodalarc.models.resolved_session import ResolvedSession, SourceContext
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.workloads.adapter import SessionContext

from adapters.frr import FRR_DAEMONS, FrrAdapter, _daemons_file
from adapters.frr.stack import resolve_domain_stack
from adapters.frr.template_vars import build_template_vars_from_resolved
from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    shipped_read_view,
)
from tests.catalog_session_fixtures import (
    resolve_catalog_session as resolve_session,
)

_ADAPTER = FrrAdapter()


def _raw_session(
    *,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    planes: int = 2,
    slots: int = 2,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_catalog_session_fixture(
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


def _files(resolved: ResolvedSession, node_id: str) -> dict[str, str]:
    node = resolved.node_by_id(node_id)
    assert node is not None
    rendered = _ADAPTER.render_node(node, SessionContext(resolved))
    return {name: content.decode() for name, content in rendered.files.items()}


def _frr_conf(resolved: ResolvedSession, node_id: str) -> str:
    return _files(resolved, node_id)["frr.conf"]


def _enabled_daemons(daemons_file: str) -> set[str]:
    return {line.split("=")[0] for line in daemons_file.splitlines() if line.endswith("=yes")}


def _vars_for(resolved: ResolvedSession, node_id: str) -> dict[str, Any]:
    node = resolved.node_by_id(node_id)
    assert node is not None
    domain = resolved.routing_domain_for(node_id)
    stack = resolve_domain_stack(domain)
    return build_template_vars_from_resolved(
        resolved,
        node,
        domain=domain,
        stack=stack,
        node_sid_index=resolved.sid_index_by_node_id().get(node_id)
        if stack.segment_routing
        else None,
    )


def _stanzas(conf: str) -> list[tuple[str, list[str]]]:
    """Top-level stanzas of an integrated configuration: header and body."""
    stanzas: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in conf.splitlines():
        if line.startswith("!"):
            continue
        if current is None:
            if line.startswith(("interface ", "router ", "mpls ldp", "bfd", "segment-routing")):
                current = (line, [])
            continue
        if line == "exit":
            stanzas.append(current)
            current = None
            continue
        current[1].append(line)
    return stanzas


def _first_satellite(resolved: ResolvedSession) -> str:
    return next(node.node_id for node in resolved.nodes if node.kind == "satellite")


def _first_ground(resolved: ResolvedSession) -> str:
    return next(node.node_id for node in resolved.nodes if node.kind == "ground_station")


def test_isis_satellite_uses_resolved_loopback_unnumbered_wan_and_sid() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr"])
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    conf = _frr_conf(resolved, node_id)

    assert f"hostname {node_id}" in conf
    assert f"ip address {vars_for_node['ipv4_loopback']}/32" in conf
    assert "interface isl0" in conf
    assert "interface gnd0" in conf
    assert "router isis NODAL" in conf
    assert "isis network point-to-point" in conf
    assert "segment-routing on" in conf
    sid = int(re.search(r"index\s+(\d+)", conf).group(1))
    assert 0 < sid <= 8000


def test_isis_ground_renders_numbered_terr0_and_unnumbered_term_interfaces() -> None:
    resolved = _resolved(protocol="isis")
    node_id = _first_ground(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    stanzas = _stanzas(_frr_conf(resolved, node_id))
    terr0 = [line for header, body in stanzas if header == "interface terr0" for line in body]
    term0 = [line for header, body in stanzas if header == "interface term0" for line in body]

    assert " ip address 172.16.0.1/24" in terr0
    assert " isis passive" in terr0
    assert f" ip address {vars_for_node['ipv4_loopback']}/32" in term0


def test_default_route_is_originated_inside_the_one_router_block() -> None:
    raw = _raw_session(protocol="isis")
    raw["segments"][1]["apply"]["originated_prefixes"] = {"ipv4": ["default"]}
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr"))

    stanzas = _stanzas(_frr_conf(resolved, _first_ground(resolved)))
    routers = [body for header, body in stanzas if header == "router isis NODAL"]

    assert "0.0.0.0/0" not in _frr_conf(resolved, _first_ground(resolved))
    assert len(routers) == 1
    assert " default-information originate ipv4 level-2 always metric 100" in routers[0]


def test_ospf_satellite_uses_resolved_point_to_point_links_te_and_ldp() -> None:
    resolved = _resolved(protocol="ospf", extensions=["te", "mpls"])
    node_id = _first_satellite(resolved)

    files = _files(resolved, node_id)
    conf = files["frr.conf"]

    assert "router ospf" in conf
    assert "mpls-te on" in conf
    assert "ip ospf network point-to-point" in conf
    assert "ip ospf cost" in conf
    assert "mpls ldp" in conf
    assert {"ospfd", "ldpd"} <= _enabled_daemons(files["daemons"])


def test_ospf_cross_area_link_uses_backbone_area_from_resolved_area_assignment() -> None:
    raw = _raw_session(protocol="ospf", planes=2, slots=1)
    raw["link_rules"][1]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "sat-p00s00", "b": "sat-p01s00"}],
    }
    raw["routing"]["domains"][0]["area_assignment"] = {"strategy": "per_plane"}
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr"))
    vars_for_node = _vars_for(resolved, "space-sat-p00s00")

    isl = next(
        entry for entry in vars_for_node["wan_interfaces"] if entry["name"].startswith("isl")
    )
    assert vars_for_node["area_id"] == "0.0.0.1"
    assert isl["ospf_area"] == "0.0.0.0"
    stanzas = _stanzas(_frr_conf(resolved, "space-sat-p00s00"))
    isl_body = [
        line for header, body in stanzas if header == f"interface {isl['name']}" for line in body
    ]
    assert " ip ospf area 0.0.0.0" in isl_body


def test_explicit_area_assignment_applies_ground_station_area() -> None:
    raw = _raw_session(protocol="isis", planes=2, slots=1)
    site = raw.read_catalog(raw.site_refs[0])["site"]
    target_local_id = f"{site['id']}-{site['nodes'][0]['id']}"
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "gs_area_id": "49.0001",
        "assignments": [
            {"planes": [0], "area_id": "49.0001"},
            {"planes": [1], "area_id": "49.0002"},
            {"ground_stations": [target_local_id], "area_id": "49.1234"},
        ],
    }

    resolved = resolve_session(raw)
    target = next(node for node in resolved.nodes if node.local_node_id == target_local_id)
    vars_for_node = _vars_for(resolved, target.node_id)

    assert vars_for_node["area_id"] == "49.1234"
    assert " net 49.1234." in _frr_conf(resolved, target.node_id)


def test_resolved_template_vars_fail_loud_when_sr_sid_is_missing() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr"])
    node_id = _first_satellite(resolved)
    domain = resolved.routing_domain_for(node_id)

    with pytest.raises(ValueError, match="SID index"):
        build_template_vars_from_resolved(
            resolved,
            resolved.node_by_id(node_id),
            domain=domain,
            stack=resolve_domain_stack(domain),
            node_sid_index=None,
        )


# ---------------------------------------------------------------------------
# Delivered files and assembly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("protocol", "extensions"),
    [
        ("isis", []),
        ("isis", ["sr", "te"]),
        ("ospf", ["mpls"]),
        ("ospf", ["sr", "te"]),
        ("static", []),
    ],
)
def test_adapter_delivers_exactly_the_integrated_configuration_files(protocol, extensions) -> None:
    resolved = _resolved(protocol=protocol, extensions=extensions)
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        files = _files(resolved, node_id)
        stack = resolve_domain_stack(resolved.routing_domain_for(node_id))

        assert set(files) == {"frr.conf", "daemons", "_config_version"}
        assert (
            files["_config_version"] == hashlib.sha256(files["frr.conf"].encode()).hexdigest()[:16]
        )
        assert [line.split("=")[0] for line in files["daemons"].splitlines()] == list(FRR_DAEMONS)
        assert _enabled_daemons(files["daemons"]) == set(stack.daemons)
        separators = [line for line in files["frr.conf"].splitlines() if line.startswith("! === ")]
        assert separators == [f"! === {fragment} ===" for fragment in stack.fragments]


@pytest.mark.parametrize(
    ("protocol", "log_file"),
    [("isis", "/var/log/frr/isisd.log"), ("ospf", "/var/log/frr/ospfd.log"), ("static", None)],
)
def test_hostname_and_logging_are_stated_once(protocol, log_file) -> None:
    resolved = _resolved(protocol=protocol, extensions=["mpls"] if protocol != "static" else [])
    node_id = _first_satellite(resolved)
    lines = _frr_conf(resolved, node_id).splitlines()

    assert lines.count(f"hostname {node_id}") == 1
    assert sum(line.startswith("hostname ") for line in lines) == 1
    assert lines.count("log syslog informational") == 1
    log_files = [line for line in lines if line.startswith("log file ")]
    assert log_files == ([f"log file {log_file} informational"] if log_file else [])


def test_each_isis_interface_is_enabled_once() -> None:
    resolved = _resolved(protocol="isis")
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        enabled = Counter(
            header
            for header, body in _stanzas(_frr_conf(resolved, node_id))
            if header.startswith("interface ")
            for line in body
            if line == " ip router isis NODAL"
        )

        assert enabled
        assert set(enabled.values()) == {1}, enabled


def test_ldp_domain_configures_ldp_on_every_wan_interface() -> None:
    resolved = _resolved(protocol="isis", extensions=["mpls"])
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    files = _files(resolved, node_id)
    [ldp] = [body for header, body in _stanzas(files["frr.conf"]) if header == "mpls ldp"]

    assert f" router-id {vars_for_node['ipv4_loopback']}" in ldp
    for entry in vars_for_node["wan_interfaces"]:
        assert f"  interface {entry['name']}" in ldp
    assert "ldpd" in _enabled_daemons(files["daemons"])
    assert "pathd" not in _enabled_daemons(files["daemons"])


def test_segment_routing_domain_runs_no_ldp() -> None:
    resolved = _resolved(protocol="isis", extensions=["mpls", "sr"])
    files = _files(resolved, _first_satellite(resolved))

    assert "mpls ldp" not in files["frr.conf"]
    assert "ldpd" not in _enabled_daemons(files["daemons"])
    assert "pathd" in _enabled_daemons(files["daemons"])


@pytest.mark.parametrize("protocol", ["isis", "ospf"])
def test_bfd_renders_the_authored_timers_through_one_profile(protocol) -> None:
    routing = {
        "domains": [
            {
                "id": "space_igp",
                "protocol": protocol,
                "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                "timers": {
                    "bfd": {
                        "enabled": True,
                        "detect_multiplier": 5,
                        "rx_interval_ms": 150,
                        "tx_interval_ms": 200,
                    }
                },
            }
        ]
    }
    resolved = _resolved(protocol=protocol, routing=routing)
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)

    files = _files(resolved, node_id)
    stanzas = _stanzas(files["frr.conf"])
    [bfd] = [body for header, body in stanzas if header == "bfd"]
    enable = " isis bfd" if protocol == "isis" else " ip ospf bfd"

    assert bfd == [
        " profile NODAL",
        "  detect-multiplier 5",
        "  receive-interval 150",
        "  transmit-interval 200",
        " exit",
    ]
    for entry in vars_for_node["wan_interfaces"]:
        body = [
            line
            for header, lines in stanzas
            if header == f"interface {entry['name']}"
            for line in lines
        ]
        assert enable in body
        assert f"{enable} profile NODAL" in body
    assert "bfdd" in _enabled_daemons(files["daemons"])


def test_bfd_disabled_runs_no_bfd_daemon_and_renders_no_bfd() -> None:
    resolved = _resolved(protocol="isis")
    files = _files(resolved, _first_satellite(resolved))

    assert "bfd" not in files["frr.conf"]
    assert "bfdd" not in _enabled_daemons(files["daemons"])


def test_daemons_file_refuses_an_empty_or_unknown_selection() -> None:
    with pytest.raises(ValueError, match="at least one daemon"):
        _daemons_file(())
    with pytest.raises(ValueError, match="unknown daemon"):
        _daemons_file(("zebra", "notad"))


def test_wan_interface_metrics_follow_link_bandwidth_and_access_links() -> None:
    resolved = _resolved(protocol="isis")
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)
    bandwidth = {
        name: candidate.bandwidth_mbps
        for candidate in resolved.link_candidates
        if candidate.kind != "access"
        for owner, name in zip(
            (candidate.node_a, candidate.node_b), candidate.fixed_interfaces, strict=True
        )
        if owner == node_id
    }

    assert bandwidth
    for entry in vars_for_node["wan_interfaces"]:
        if entry["name"] in bandwidth:
            assert entry["metric"] == int(10000 / float(bandwidth[entry["name"]]))
        else:
            assert entry["name"].startswith("gnd")
            assert entry["metric"] == 10


# ---------------------------------------------------------------------------
# Identity and multi-domain rendering nets
# ---------------------------------------------------------------------------

NET_LINE = re.compile(
    r"^ net (?P<area>[0-9a-f.]+)\.(?P<system>[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\.00$"
)


def test_rendered_nets_never_use_the_reserved_all_zero_system_id() -> None:
    """The all-zero system id is reserved in IS-IS: FRR floods an LSP from
    such a router but remote SPF never installs its prefixes, so the node is
    reachable only by direct neighbors. Rendered through the FRR adapter for
    every IS-IS domain member of the session that exposed the failure live,
    no NET may carry it, and the first-resolved node renders the first id."""
    resolution = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-luna-dtn.yaml"), catalog=shipped_read_view()
    )
    resolved = resolution.resolved
    index = resolved.node_index_by_node_id()

    system_by_node: dict[str, str] = {}
    for domain in resolved.routing_domains:
        for node_id in domain.node_ids:
            rendered = _frr_conf(resolved, node_id)
            nets = [m for m in map(NET_LINE.match, rendered.splitlines()) if m]
            assert nets, f"{node_id}: no parseable NET in rendered frr.conf"
            for match in nets:
                assert match.group("system") != "0000.0000.0000", (
                    f"{node_id} rendered the reserved all-zero IS-IS system id"
                )
            system_by_node[node_id] = nets[0].group("system")

    assert system_by_node, "session rendered no IS-IS domain members"
    first = min(system_by_node, key=lambda node_id: index[node_id])
    assert index[first] == 0, "fixture no longer places a routed node at index 0"
    assert system_by_node[first] == "0000.0000.0001"


def test_isis_system_ids_are_globally_unique_across_segments() -> None:
    """Four segments share plane/slot numbering in one IS-IS domain; every
    rendered NET must still be unique — identity comes from the resolver,
    never from per-segment indices."""
    resolution = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml"),
        catalog=shipped_read_view(),
    )
    resolved = resolution.resolved

    nets: dict[str, str] = {}
    for domain in resolved.routing_domains:
        for node_id in domain.node_ids:
            rendered = _frr_conf(resolved, node_id)
            net_lines = [line for line in rendered.splitlines() if NET_LINE.match(line)]
            assert len(net_lines) == 1, f"{node_id}: expected one NET in rendered frr.conf"
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

    sat_files = _files(resolved, _first_satellite(resolved))
    ground_files = _files(resolved, _first_ground(resolved))
    assert "isisd" in _enabled_daemons(sat_files["daemons"])
    assert "ospfd" not in _enabled_daemons(sat_files["daemons"])
    assert "ospfd" in _enabled_daemons(ground_files["daemons"])
    assert "isisd" not in _enabled_daemons(ground_files["daemons"])
    assert "router isis NODAL" in sat_files["frr.conf"]
    assert " ip router isis NODAL" in sat_files["frr.conf"]
    assert "router ospf" in ground_files["frr.conf"]
    assert " ip ospf area" in ground_files["frr.conf"]


def test_static_domain_renders_zebra_and_staticd_only() -> None:
    resolved = resolve_session(
        _raw_session(protocol="static"),
        source_context=SourceContext(origin="test.frr", run_id="run-test-0001"),
    )
    sat_id = _first_satellite(resolved)

    files = _files(resolved, sat_id)

    assert _enabled_daemons(files["daemons"]) == {"mgmtd", "zebra", "staticd"}
    # No IGP statements leak into a static-domain configuration.
    assert "isis" not in files["frr.conf"]
    assert "ospf" not in files["frr.conf"]
    assert f"hostname {sat_id}" in files["frr.conf"]


def test_boundary_exports_materialize_on_flagship_border_nodes() -> None:
    """The cislunar session's declared purpose is Earth<->Luna reachability:
    boundary exports must render as installable static routes plus IGP
    redistribution on the border nodes — declared intent, materialized."""
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml"),
        catalog=shipped_read_view(),
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
        f"{entry['peer_loopback_ipv4']}/32"
        for entry in vars_for_node["wan_interfaces"]
        if entry["static_only"]
    }
    assert peer_seeds
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

    conf = _frr_conf(resolved, luna_border)
    sample = sorted(route_prefixes)[0]
    assert f"ip route {sample} " in conf
    assert "redistribute ipv4 static level-2" in conf
    for seed in peer_seeds:
        assert f"ip route {seed} " in conf


def test_non_border_nodes_render_no_boundary_routes_or_redistribution() -> None:
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml"), catalog=shipped_read_view()
    ).resolved
    node_id = _first_satellite(resolved)
    vars_for_node = _vars_for(resolved, node_id)
    assert vars_for_node["boundary_static_routes"] == []
    assert vars_for_node["redistribute_static"] is False
    assert "redistribute" not in _frr_conf(resolved, node_id)


def _simple_session_with_bfd(protocol: str) -> ResolvedSession:
    """earth-leo-simple with one BFD-enabled domain over every node.

    Its Denver site wires two routed gateways to one LAN, so their terr0 runs
    the IGP actively; every other site has one gateway and a passive terr0.
    """
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml").read_text(encoding="utf-8")
    )
    raw["routing"] = {
        "domains": [
            {
                "id": "leo_domain",
                "protocol": protocol,
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
                "timers": {
                    "bfd": {
                        "enabled": True,
                        "detect_multiplier": 4,
                        "rx_interval_ms": 120,
                        "tx_interval_ms": 180,
                    }
                },
            }
        ]
    }
    return resolve_session(raw, source_context=SourceContext(origin="test.frr.bfd"))


def _stanza_lines(conf: str, header: str) -> list[str]:
    return [line for name, body in _stanzas(conf) if name == header for line in body]


@pytest.mark.parametrize(
    ("protocol", "igp_line", "passive_line", "enable"),
    [
        ("isis", " ip router isis NODAL", " isis passive", " isis bfd"),
        ("ospf", " ip ospf area 0.0.0.0", " ip ospf passive", " ip ospf bfd"),
    ],
)
def test_bfd_covers_every_active_igp_interface_and_no_passive_one(
    protocol, igp_line, passive_line, enable
) -> None:
    resolved = _simple_session_with_bfd(protocol)
    active_site = "earth-us-co-denver-gw1"
    passive_site = "earth-de-frankfurt-gw1"
    satellite = _first_satellite(resolved)

    active_conf = _frr_conf(resolved, active_site)
    passive_conf = _frr_conf(resolved, passive_site)
    satellite_conf = _frr_conf(resolved, satellite)

    # Active site LAN: IGP active, BFD enabled with the domain profile.
    active_lan = _stanza_lines(active_conf, "interface terr0")
    assert igp_line in active_lan
    assert passive_line not in active_lan
    assert enable in active_lan
    assert f"{enable} profile NODAL" in active_lan
    # Passive site LAN and loopbacks: no BFD.
    passive_lan = _stanza_lines(passive_conf, "interface terr0")
    assert igp_line in passive_lan
    assert passive_line in passive_lan
    assert not any("bfd" in line for line in passive_lan)
    for conf in (active_conf, passive_conf, satellite_conf):
        assert not any("bfd" in line for line in _stanza_lines(conf, "interface lo"))
    # Active WAN links on ground and space nodes.
    for node_id, conf in ((active_site, active_conf), (satellite, satellite_conf)):
        wan = _vars_for(resolved, node_id)["wan_interfaces"]
        assert wan
        for entry in wan:
            body = _stanza_lines(conf, f"interface {entry['name']}")
            assert enable in body
            assert f"{enable} profile NODAL" in body
    # One profile carries the authored timers.
    assert _stanza_lines(active_conf, "bfd") == [
        " profile NODAL",
        "  detect-multiplier 4",
        "  receive-interval 120",
        "  transmit-interval 180",
        " exit",
    ]


def test_bfd_on_an_active_igp_interface_without_ipv4_is_refused() -> None:
    resolved = _simple_session_with_bfd("isis")
    node = resolved.node_by_id("earth-us-co-denver-gw1")
    assert node is not None and node.interfaces is not None
    terr0 = node.interfaces.ethernet["terr0"]
    v6_only = node.model_copy(
        update={
            "interfaces": node.interfaces.model_copy(
                update={
                    "ethernet": {
                        **node.interfaces.ethernet,
                        "terr0": terr0.model_copy(update={"ipv4": None}),
                    }
                }
            )
        }
    )
    session = resolved.model_copy(
        update={
            "nodes": tuple(
                v6_only if item.node_id == node.node_id else item for item in resolved.nodes
            )
        }
    )

    with pytest.raises(ValueError, match="'terr0', which has no IPv4 address"):
        _ADAPTER.render_node(v6_only, SessionContext(session))


def _router_block(conf: str, header: str) -> list[str]:
    [body] = [lines for name, lines in _stanzas(conf) if name == header]
    return body


@pytest.mark.parametrize("extensions", [["te"], ["sr", "te"], ["mpls", "te"]])
def test_isis_traffic_engineering_enables_mpls_te_on_the_router(extensions) -> None:
    resolved = _resolved(protocol="isis", extensions=extensions)
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        loopback = _vars_for(resolved, node_id)["ipv4_loopback"]
        router = _router_block(_frr_conf(resolved, node_id), "router isis NODAL")

        assert " mpls-te on" in router
        assert f" mpls-te router-address {loopback}" in router


def test_isis_without_traffic_engineering_renders_no_mpls_te() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr", "mpls"])

    assert "mpls-te" not in _frr_conf(resolved, _first_satellite(resolved))


@pytest.mark.parametrize("extensions", [["sr"], ["sr", "te"], ["mpls", "sr"]])
def test_ospf_segment_routing_advertises_resolver_prefix_sids(extensions) -> None:
    resolved = _resolved(protocol="ospf", extensions=extensions)
    sid_by_node = resolved.sid_index_by_node_id()
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        loopback = _vars_for(resolved, node_id)["ipv4_loopback"]
        files = _files(resolved, node_id)
        router = _router_block(files["frr.conf"], "router ospf")

        assert " capability opaque" in router
        assert " segment-routing on" in router
        assert " segment-routing global-block 16000 23999 local-block 40000 49999" in router
        assert f" segment-routing prefix {loopback}/32 index {sid_by_node[node_id]}" in router
        assert "pathd" in _enabled_daemons(files["daemons"])
        # SR-MPLS carries the MPLS data plane, so LDP does not run.
        assert "ldpd" not in _enabled_daemons(files["daemons"])
        assert "mpls ldp" not in files["frr.conf"]


def test_ospf_prefix_sids_are_unique_across_the_domain() -> None:
    resolved = _resolved(protocol="ospf", extensions=["sr"])
    [domain] = resolved.routing_domains

    indices = [
        line.rsplit(" ", 1)[1]
        for node_id in domain.node_ids
        for line in _router_block(_frr_conf(resolved, node_id), "router ospf")
        if line.startswith(" segment-routing prefix ")
    ]

    assert len(indices) == len(domain.node_ids)
    assert len(set(indices)) == len(indices)


def test_ospf_traffic_engineering_enables_opaque_lsas() -> None:
    resolved = _resolved(protocol="ospf", extensions=["te"])
    node_id = _first_satellite(resolved)
    loopback = _vars_for(resolved, node_id)["ipv4_loopback"]
    router = _router_block(_frr_conf(resolved, node_id), "router ospf")

    assert router.index(" capability opaque") < router.index(" mpls-te on")
    assert f" mpls-te router-address {loopback}" in router
    assert not any(line.startswith(" segment-routing") for line in router)


def test_plain_ospf_renders_no_opaque_capability() -> None:
    resolved = _resolved(protocol="ospf", extensions=["mpls"])

    assert "capability opaque" not in _frr_conf(resolved, _first_satellite(resolved))


def _tdrs_session(capabilities: dict[str, Any] | None) -> ResolvedSession:
    """earth-geo-tdrs, whose TDRS terminals send and receive at different rates."""
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-geo-tdrs.yaml").read_text(encoding="utf-8")
    )
    domain: dict[str, Any] = {
        "id": "geo_domain",
        "protocol": "isis",
        "selectors": [{"any": [{"segment": "geo"}, {"segment": "ground"}]}],
    }
    if capabilities is not None:
        domain["capabilities"] = capabilities
    raw["routing"] = {"domains": [domain]}
    return resolve_session(raw, source_context=SourceContext(origin="test.frr.te"))


def _link_params(conf: str, interface: str) -> list[str]:
    body = _stanza_lines(conf, f"interface {interface}")
    if " link-params" not in body:
        return []
    return body[body.index(" link-params") + 1 : body.index(" exit-link-params")]


def test_resolved_terminals_keep_their_own_transmit_and_receive_rates() -> None:
    resolved = _tdrs_session(None)
    relay = {b.terminal_id: b for b in resolved.node_by_id("geo-tdrs-041w").terminal_inventory}
    ground = {
        b.terminal_id: b for b in resolved.node_by_id("earth-it-fucino-gw1").terminal_inventory
    }

    assert (relay["s_sa"].transmit_mbps, relay["s_sa"].receive_mbps) == (14.0, 23.6)
    assert (relay["ku_sa"].transmit_mbps, relay["ku_sa"].receive_mbps) == (50.0, 600.0)
    assert (ground["tdrs_ka_sa"].transmit_mbps, ground["tdrs_ka_sa"].receive_mbps) == (
        600.0,
        50.0,
    )


@pytest.mark.parametrize(
    ("node_id", "interface", "transmit_mbps"),
    [
        # Each end advertises what its own terminal can send.
        ("geo-tdrs-041w", "gnd1", 14.0),
        ("geo-tdrs-041w", "gnd0", 3.0),
        ("earth-it-fucino-gw1", "term2", 600.0),
        ("earth-it-fucino-gw1", "term0", 750.0),
    ],
)
def test_traffic_engineering_advertises_each_terminals_transmit_rate(
    node_id, interface, transmit_mbps
) -> None:
    resolved = _tdrs_session({"traffic_engineering": {}})
    max_bw = transmit_mbps * 1_000_000 / 8
    reservable = f"{max_bw * 0.98:g}"

    assert _link_params(_frr_conf(resolved, node_id), interface) == [
        "  enable",
        f"  max-bw {max_bw:g}",
        f"  max-rsv-bw {reservable}",
        *(f"  unrsv-bw {priority} {reservable}" for priority in range(8)),
    ]


def test_link_parameters_only_on_traffic_engineering_igp_links() -> None:
    plain = _tdrs_session(None)
    te = _tdrs_session({"traffic_engineering": {}})

    assert "link-params" not in _frr_conf(plain, "earth-it-fucino-gw1")
    te_conf = _frr_conf(te, "earth-it-fucino-gw1")
    # A site LAN carries no terminal and no link parameters.
    assert _link_params(te_conf, "terr0") == []
    assert _link_params(te_conf, "lo") == []


def test_traffic_engineering_without_a_terminal_rate_is_refused() -> None:
    resolved = _tdrs_session({"traffic_engineering": {}})
    node = resolved.node_by_id("geo-tdrs-041w")
    stripped = node.model_copy(
        update={
            "terminal_inventory": tuple(
                block.model_copy(update={"transmit_mbps": None, "receive_mbps": None})
                for block in node.terminal_inventory
            )
        }
    )
    session = resolved.model_copy(
        update={
            "nodes": tuple(
                stripped if item.node_id == node.node_id else item for item in resolved.nodes
            )
        }
    )

    with pytest.raises(ValueError, match="has no terminal transmit rate"):
        _ADAPTER.render_node(stripped, SessionContext(session))
