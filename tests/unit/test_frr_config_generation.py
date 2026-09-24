# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR configuration rendered from catalog-resolved runtime truth."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from nodalarc.models.resolved_session import (
    IsisInstanceAreas,
    OspfInstanceAreas,
    ResolvedSession,
    SourceContext,
)
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.workloads.adapter import SessionContext

from adapters.frr.adapter import FRR_DAEMONS, FrrAdapter, _daemons_file
from adapters.frr.stack import resolve_router_stack
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


# The fixture sessions' one routing domain, and the domain the resolver
# declares for a shipped session without routing.
_FIXTURE_DOMAIN = "test_domain"
_DEFAULT_DOMAIN = "default_domain"


def _vars_for(resolved: ResolvedSession, node_id: str) -> dict[str, Any]:
    node = resolved.node_by_id(node_id)
    assert node is not None
    return build_template_vars_from_resolved(
        resolved,
        node,
        domains=resolved.routing_domains_for(node_id),
        sid_by_domain=resolved.sid_index_by_domain(),
    )


def _domain_vars(resolved: ResolvedSession, node_id: str) -> dict[str, Any]:
    """The template facts of the one domain ``node_id`` participates in."""
    [domain] = _vars_for(resolved, node_id)["domains"]
    return domain


def _static_links(resolved: ResolvedSession, node_id: str) -> list[dict[str, Any]]:
    return _vars_for(resolved, node_id)["static_links"]


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
    assert f"router isis {_FIXTURE_DOMAIN}" in conf
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
    routers = [body for header, body in stanzas if header == f"router isis {_FIXTURE_DOMAIN}"]

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


def test_ospf_renders_every_interface_in_its_resolved_area(monkeypatch) -> None:
    """The adapter renders the resolved OSPF areas and decides none itself.

    The session grammar assigns areas per router, so this router's areas are
    supplied as resolved facts: its access interfaces in the backbone, its
    loopback and other interfaces in area 0.0.0.1, which makes it an ABR.
    """
    resolved = _resolved(protocol="ospf")
    node_id = _first_satellite(resolved)
    node = resolved.node_by_id(node_id)
    assert node is not None
    resolved_areas = ResolvedSession.instance_areas_by_node

    def with_an_abr(self):
        areas = resolved_areas(self)
        [ospf] = areas[node_id]
        areas[node_id] = (
            OspfInstanceAreas(
                ospf.domain_id,
                loopback_area="0.0.0.1",
                interface_areas={
                    name: "0.0.0.0" if name in node.access_interfaces else "0.0.0.1"
                    for name in ospf.interface_areas
                },
            ),
        )
        return areas

    monkeypatch.setattr(ResolvedSession, "instance_areas_by_node", with_an_abr)
    [ospf] = resolved.instance_areas_by_node()[node_id]
    assert ospf.area_border
    conf = _frr_conf(resolved, node_id)

    assert " ip ospf area 0.0.0.1" in _stanza_lines(conf, "interface lo")
    for name, area in ospf.interface_areas.items():
        assert f" ip ospf area {area}" in _stanza_lines(conf, f"interface {name}")
    assert {"0.0.0.0", "0.0.0.1"} == set(ospf.interface_areas.values())


def test_isis_renders_a_net_for_each_area_address(monkeypatch) -> None:
    resolved = _resolved(protocol="isis")
    node_id = _first_satellite(resolved)
    resolved_areas = ResolvedSession.instance_areas_by_node

    def two_addresses(self):
        areas = resolved_areas(self)
        [isis] = areas[node_id]
        areas[node_id] = (IsisInstanceAreas(isis.domain_id, ("49.0001", "49.0002")),)
        return areas

    monkeypatch.setattr(ResolvedSession, "instance_areas_by_node", two_addresses)
    router = _router_block(_frr_conf(resolved, node_id), f"router isis {_FIXTURE_DOMAIN}")

    # A NET is the area address, the system id (three groups) and the selector.
    nets = [line.removeprefix(" net ") for line in router if line.startswith(" net ")]
    assert [net.rsplit(".", 4)[0] for net in nets] == ["49.0001", "49.0002"]


def test_a_single_ospf_area_outside_the_backbone_renders_on_every_interface() -> None:
    raw = _raw_session(protocol="ospf", planes=2, slots=1)
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "assignments": [
            {"planes": [0, 1], "area_id": "0.0.0.5"},
            {"ground_stations": "all", "area_id": "0.0.0.5"},
        ],
    }
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr"))

    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        conf = _frr_conf(resolved, node_id)
        areas = {line.strip() for line in conf.splitlines() if line.startswith(" ip ospf area ")}
        assert areas == {"ip ospf area 0.0.0.5"}


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

    assert _domain_vars(resolved, target.node_id)["area_addresses"] == ("49.1234",)
    assert " net 49.1234." in _frr_conf(resolved, target.node_id)


def test_resolved_template_vars_fail_loud_when_sr_sid_is_missing() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr"])
    node_id = _first_satellite(resolved)
    sid_by_domain = resolved.sid_index_by_domain()
    without_node = {
        domain_id: {member: sid for member, sid in sids.items() if member != node_id}
        for domain_id, sids in sid_by_domain.items()
    }

    with pytest.raises(ValueError, match=f"no resolved SID index for {node_id!r}"):
        build_template_vars_from_resolved(
            resolved,
            resolved.node_by_id(node_id),
            domains=resolved.routing_domains_for(node_id),
            sid_by_domain=without_node,
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
        node = resolved.node_by_id(node_id)
        assert node is not None
        stack = resolve_router_stack(resolved.routing_domains_for(node_id), node.address_families)

        assert set(files) == {"frr.conf", "daemons"}
        assert [line.split("=")[0] for line in files["daemons"].splitlines()] == list(FRR_DAEMONS)
        assert _enabled_daemons(files["daemons"]) == set(stack.daemons)
        separators = [line for line in files["frr.conf"].splitlines() if line.startswith("! === ")]
        assert separators == [f"! === {fragment} ===" for fragment in stack.fragments]


@pytest.mark.parametrize("protocol", ["isis", "ospf", "static"])
def test_hostname_and_logging_are_stated_once(protocol) -> None:
    resolved = _resolved(protocol=protocol, extensions=["mpls"] if protocol != "static" else [])
    node_id = _first_satellite(resolved)
    lines = _frr_conf(resolved, node_id).splitlines()

    assert lines.count(f"hostname {node_id}") == 1
    assert sum(line.startswith("hostname ") for line in lines) == 1
    assert lines.count("log syslog informational") == 1
    # Every daemon logs to the one file the measurement adapters read.
    log_files = [line for line in lines if line.startswith("log file ")]
    assert log_files == ["log file /var/log/frr/frr.log informational"]


def test_each_isis_interface_is_enabled_once() -> None:
    resolved = _resolved(protocol="isis")
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        enabled = Counter(
            header
            for header, body in _stanzas(_frr_conf(resolved, node_id))
            if header.startswith("interface ")
            for line in body
            if line == f" ip router isis {_FIXTURE_DOMAIN}"
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
        " profile space_igp",
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
        assert f"{enable} profile space_igp" in body
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


def test_wan_interface_metrics_follow_own_transmit_rate_and_access_links() -> None:
    resolved = _resolved(protocol="isis")
    node_id = _first_satellite(resolved)
    domain_vars = _domain_vars(resolved, node_id)
    rates = resolved.interface_terminal_rates()
    fixed = {
        name
        for candidate in resolved.link_candidates
        if candidate.kind != "access"
        for owner, name in zip(
            (candidate.node_a, candidate.node_b), candidate.fixed_interfaces, strict=True
        )
        if owner == node_id
    }

    assert fixed
    for entry in domain_vars["interfaces"]:
        if entry["name"] in fixed:
            # The fixture's ISL terminal transmits 2000 Mb/s: 100000 / 2000.
            assert rates[(node_id, entry["name"])].transmit_mbps == 2000.0
            assert entry["metric"] == 50
        else:
            assert entry["name"].startswith("gnd")
            assert entry["metric"] == 10


@pytest.mark.parametrize(
    ("transmit_mbps", "metric"),
    [
        (200_000, 1),
        (100_000, 1),
        (10_000, 10),
        (3_500, 28),
        (1_000, 100),
        (260, 384),
        (3, 33_333),
    ],
)
def test_the_igp_metric_is_100_gbps_over_transmit_and_never_below_one(
    transmit_mbps: float, metric: int
) -> None:
    from adapters.frr import template_vars

    node = _node_with_isl_transmit(transmit_mbps)

    assert template_vars._igp_metric(node, "isl0", "isis") == metric
    assert template_vars._igp_metric(node, "isl0", "ospf") == metric


def test_a_metric_above_the_protocol_maximum_is_refused() -> None:
    from adapters.frr import template_vars

    node = _node_with_isl_transmit(1.0)

    assert template_vars._igp_metric(node, "isl0", "isis") == 100_000
    with pytest.raises(ValueError, match="ospf metric 100000 exceeds the protocol maximum 65535"):
        template_vars._igp_metric(node, "isl0", "ospf")


def _node_with_isl_transmit(transmit_mbps: float):
    resolved = _resolved(protocol="isis")
    node = resolved.node_by_id(_first_satellite(resolved))
    assert node is not None
    wan = next(wan for wan in node.wan_interfaces if wan.name == "isl0")
    inventory = tuple(
        block.model_copy(update={"transmit_mbps": transmit_mbps})
        if block.terminal_id == wan.terminal_id
        else block
        for block in node.terminal_inventory
    )
    return node.model_copy(update={"terminal_inventory": inventory})


def test_every_shipped_session_renders_valid_igp_metrics() -> None:
    from nodalarc.catalog_closure import FilesystemCatalogReadView
    from nodalarc.catalog_paths import CatalogRoots

    from adapters.frr import template_vars

    catalog = FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc"))
    sessions = sorted(Path("catalog/nodalarc/sessions").glob("*.yaml"))
    assert sessions
    checked = 0
    for path in sessions:
        resolved = load_session_resolution_from_file(path, catalog=catalog).resolved
        for domain in resolved.routing_domains:
            maximum = template_vars._MAXIMUM_IGP_METRIC.get(domain.protocol)
            if maximum is None:
                continue
            for node_id in domain.node_ids:
                [facts] = [
                    facts
                    for facts in _vars_for(resolved, node_id)["domains"]
                    if facts["domain_id"] == domain.domain_id
                ]
                for entry in facts["interfaces"]:
                    assert 1 <= entry["metric"] <= maximum, (path.name, node_id, entry)
                    checked += 1
    assert checked


def test_each_end_of_an_asymmetric_fixed_link_carries_its_own_metric() -> None:
    """The metric of a link end follows what that end transmits, not the peer."""
    resolved = _resolved(protocol="isis")
    candidate = next(c for c in resolved.link_candidates if c.kind != "access")
    interface_a, interface_b = candidate.fixed_interfaces
    node_a = resolved.node_by_id(candidate.node_a)
    assert node_a is not None
    terminal_a = next(w.terminal_id for w in node_a.wan_interfaces if w.name == interface_a)
    # End A's terminal transmits at 100 Mbit/s and still receives at its
    # declared rate; end B keeps its declared terminal.
    slow_a = node_a.model_copy(
        update={
            "terminal_inventory": tuple(
                block.model_copy(update={"transmit_mbps": 100.0})
                if block.terminal_id == terminal_a
                else block
                for block in node_a.terminal_inventory
            )
        }
    )
    asymmetric = resolved.model_copy(
        update={
            "nodes": tuple(slow_a if n.node_id == node_a.node_id else n for n in resolved.nodes)
        }
    )
    b_transmit = asymmetric.interface_terminal_rates()[
        (candidate.node_b, interface_b)
    ].transmit_mbps
    assert b_transmit == 2000.0

    metric_a = next(
        e["metric"]
        for e in _domain_vars(asymmetric, candidate.node_a)["interfaces"]
        if e["name"] == interface_a
    )
    metric_b = next(
        e["metric"]
        for e in _domain_vars(asymmetric, candidate.node_b)["interfaces"]
        if e["name"] == interface_b
    )

    # 100000 / 100 on end A; 100000 / 2000 on end B.
    assert metric_a == 1000
    assert metric_b == 50


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
    assert "router isis space_igp" in sat_files["frr.conf"]
    assert " ip router isis space_igp" in sat_files["frr.conf"]
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
    peer_seeds = {f"{entry['peer_loopback_ipv4']}/32" for entry in vars_for_node["static_links"]}
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
    assert _domain_vars(resolved, node_id)["redistribute_static"] == ()
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
        ("isis", " ip router isis leo_domain", " isis passive", " isis bfd"),
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
    assert f"{enable} profile leo_domain" in active_lan
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
            assert f"{enable} profile leo_domain" in body
    # One profile carries the authored timers.
    assert _stanza_lines(active_conf, "bfd") == [
        " profile leo_domain",
        "  detect-multiplier 4",
        "  receive-interval 120",
        "  transmit-interval 180",
        " exit",
    ]


def _router_block(conf: str, header: str) -> list[str]:
    [body] = [lines for name, lines in _stanzas(conf) if name == header]
    return body


@pytest.mark.parametrize("extensions", [["te"], ["sr", "te"], ["mpls", "te"]])
def test_isis_traffic_engineering_enables_mpls_te_on_the_router(extensions) -> None:
    resolved = _resolved(protocol="isis", extensions=extensions)
    for node_id in (_first_satellite(resolved), _first_ground(resolved)):
        loopback = _vars_for(resolved, node_id)["ipv4_loopback"]
        router = _router_block(_frr_conf(resolved, node_id), f"router isis {_FIXTURE_DOMAIN}")

        assert " mpls-te on" in router
        assert f" mpls-te router-address {loopback}" in router


def test_isis_without_traffic_engineering_renders_no_mpls_te() -> None:
    resolved = _resolved(protocol="isis", extensions=["sr", "mpls"])

    assert "mpls-te" not in _frr_conf(resolved, _first_satellite(resolved))


@pytest.mark.parametrize("extensions", [["sr"], ["sr", "te"], ["mpls", "sr"]])
def test_ospf_segment_routing_advertises_resolver_prefix_sids(extensions) -> None:
    resolved = _resolved(protocol="ospf", extensions=extensions)
    sid_by_node = resolved.sid_index_by_domain()[_FIXTURE_DOMAIN]
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


def _simple_session() -> ResolvedSession:
    return load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml"), catalog=shipped_read_view()
    ).resolved


def _denver_router(resolved: ResolvedSession) -> str:
    return next(
        node.node_id for node in resolved.nodes if node.node_id.startswith("earth-us-co-denver-gw")
    )


def test_an_ipv4_only_router_renders_no_ipv6_at_all() -> None:
    resolved = _simple_session()
    satellite = resolved.node_by_id(_first_satellite(resolved))
    assert satellite is not None and satellite.address_families == {"ipv4"}

    conf = _frr_conf(resolved, satellite.node_id)

    assert "ipv6" not in conf
    assert "topology ipv6-unicast" not in conf
    # Forwarding is substrate state, and no router advertisements are sent.
    assert "forwarding" not in conf
    assert "suppress-ra" not in conf


def test_a_router_on_a_declared_ipv6_lan_routes_ipv6_over_the_lan_only() -> None:
    resolved = _simple_session()
    router_id = _denver_router(resolved)
    router = resolved.node_by_id(router_id)
    assert router is not None and router.interfaces is not None
    assert router.address_families == {"ipv4", "ipv6"}
    assert router.interfaces.lo0.ipv6 is None
    terr0 = router.interfaces.ethernet["terr0"]
    assert terr0.ipv6 is not None

    conf = _frr_conf(resolved, router_id)

    assert " topology ipv6-unicast" in _router_block(conf, f"router isis {_DEFAULT_DOMAIN}")
    for wan in router.wan_interfaces:
        lines = _stanza_lines(conf, f"interface {wan.name}")
        assert f" ip router isis {_DEFAULT_DOMAIN}" in lines
        # Its ground terminals reach only IPv4-only satellites.
        assert f" ipv6 router isis {_DEFAULT_DOMAIN}" not in lines
        # No IPv6 loopback is declared, so the WAN borrows none.
        assert not [line for line in lines if line.startswith(" ipv6 address")]
    lan = _stanza_lines(conf, "interface terr0")
    assert f" ipv6 address {terr0.ipv6}" in lan
    assert f" ip router isis {_DEFAULT_DOMAIN}" in lan
    assert f" ipv6 router isis {_DEFAULT_DOMAIN}" in lan
    # The loopback holds no IPv6 address, so it joins IPv6 routing nowhere.
    assert f" ipv6 router isis {_DEFAULT_DOMAIN}" not in _stanza_lines(conf, "interface lo")


def test_default_origination_renders_per_declared_family() -> None:
    from nodalarc.configuration_yaml import load_configuration_yaml

    # The shipped sites originate an IPv4 default only.
    simple = _simple_session()
    plain = _router_block(
        _frr_conf(simple, _denver_router(simple)), f"router isis {_DEFAULT_DOMAIN}"
    )
    assert " default-information originate ipv4 level-2 always metric 100" in plain
    assert not [line for line in plain if "originate ipv6" in line]

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml").read_text(encoding="utf-8")
    )
    ground = next(segment for segment in raw["segments"] if segment["id"] == "ground")
    ground["apply"] = {
        **(ground.get("apply") or {}),
        "originated_prefixes": {"ipv6": ["default"]},
    }
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr.ipv6"))

    router = _router_block(
        _frr_conf(resolved, _denver_router(resolved)), f"router isis {_DEFAULT_DOMAIN}"
    )

    assert " default-information originate ipv4 level-2 always metric 100" in router
    assert " default-information originate ipv6 level-2 always metric 100" in router


def test_ospf_runs_ospfv3_beside_ospfv2_on_ipv6_routers_only() -> None:
    resolved = _simple_session_with_bfd("ospf")
    satellite_id = _first_satellite(resolved)
    router_id = _denver_router(resolved)
    router = resolved.node_by_id(router_id)
    assert router is not None

    satellite_files = _files(resolved, satellite_id)
    assert "ospf6d" not in _enabled_daemons(satellite_files["daemons"])
    assert "ospf6" not in satellite_files["frr.conf"]

    files = _files(resolved, router_id)
    assert {"ospfd", "ospf6d", "bfdd"} <= _enabled_daemons(files["daemons"])
    conf = files["frr.conf"]
    area = _domain_vars(resolved, router_id)["loopback_area"]
    ospf6 = _router_block(conf, "router ospf6")
    assert f" ospf6 router-id {_vars_for(resolved, router_id)['ipv4_loopback']}" in ospf6
    for wan in router.wan_interfaces:
        lines = _stanza_lines(conf, f"interface {wan.name}")
        assert " ipv6 ospf6 network point-to-point" in lines
        assert " ipv6 ospf6 area 0.0.0.0" in lines
        assert " ipv6 ospf6 cost 10" in lines
        assert " ipv6 ospf6 bfd profile leo_domain" in lines
        assert [line for line in lines if line.startswith(" ipv6 ospf6 hello-interval")]
        assert [line for line in lines if line.startswith(" ipv6 ospf6 dead-interval")]
    # Denver wires two routers to one LAN, so the LAN runs OSPFv3 actively.
    lan = _stanza_lines(conf, "interface terr0")
    assert f" ipv6 ospf6 area {area}" in lan
    assert " ipv6 ospf6 passive" not in lan
    assert " ipv6 ospf6 bfd profile leo_domain" in lan
    assert f" ip ospf area {area}" in lan


def _reachability_with_ipv6_loopbacks() -> ResolvedSession:
    """The cislunar session with an IPv6 loopback declared beside every IPv4 one."""
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml").read_text(
            encoding="utf-8"
        )
    )
    [ipv4] = raw["addressing"]["loopbacks"]
    raw["addressing"]["loopbacks"].append(
        {
            "id": "node_loopbacks_v6",
            "applies_to": ipv4["applies_to"],
            "ipv6_pool": "fd00:6e1::/64",
            "prefix_length": 128,
        }
    )
    return resolve_session(raw, source_context=SourceContext(origin="test.frr.ipv6"))


def _reachability_with_ipv6_on_one_leo_a_satellite(*, bfd: bool) -> dict[str, Any]:
    """The cislunar session with an IPv6 loopback on the leo_a satellite in slot
    0 only. Its ground stations carry IPv6 from their site LANs, so each leo_a
    ground terminal reaches one satellite that carries IPv6 and others that do
    not."""
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw["addressing"]["loopbacks"].append(
        {
            "id": "leo_a_slot0_loopbacks_v6",
            "applies_to": {"all": [{"segment": "leo_a"}, {"slot": 0}]},
            "ipv6_pool": "fd00:6e2::/64",
            "prefix_length": 128,
        }
    )
    if bfd:
        earth = next(d for d in raw["routing"]["domains"] if d["id"] == "earth_domain")
        earth["timers"] = {"bfd": {"enabled": True}}
    return raw


def test_ipv6_is_is_runs_on_an_interface_only_where_a_peer_carries_ipv6() -> None:
    resolved = resolve_session(
        _reachability_with_ipv6_on_one_leo_a_satellite(bfd=False),
        source_context=SourceContext(origin="test.frr.ipv6.mixed"),
    )
    carrier = _frr_conf(resolved, "leo-a-sat-p00s00")
    # Its ground terminal reaches ground stations that carry IPv6; its ISLs
    # reach the slot 1 and slot 35 satellites, which do not.
    assert " ipv6 router isis earth_domain" in _stanza_lines(carrier, "interface gnd0")
    for interface in ("isl0", "isl1"):
        assert " ipv6 router isis earth_domain" not in _stanza_lines(
            carrier, f"interface {interface}"
        )
    # Denver's ground terminals reach the slot 0 satellite among others.
    denver = _frr_conf(resolved, "earth-us-co-denver-gw1")
    assert " ipv6 router isis earth_domain" in _stanza_lines(denver, "interface term0")


def test_is_is_bfd_is_refused_where_an_interface_reaches_ipv6_and_ipv4_only_peers() -> None:
    from nodalarc.runtime_support import UnsupportedFeatureError

    with pytest.raises(UnsupportedFeatureError) as refused:
        resolve_session(
            _reachability_with_ipv6_on_one_leo_a_satellite(bfd=True),
            source_context=SourceContext(origin="test.frr.ipv6.mixed.bfd"),
        )
    (feature,) = refused.value.features
    assert feature.value == "isis:bfd"
    assert "earth-au-perth-gw1 term0" in feature.message
    assert "adapter 'frr'" in feature.message


def _luna_border(resolved: ResolvedSession) -> str:
    boundary = resolved.routing.boundaries[0]
    luna_domain = next(d for d in resolved.routing_domains if d.domain_id == "luna_domain")
    return next(
        node_id
        for candidate in resolved.link_candidates
        if candidate.rule_id == boundary.over
        for node_id in (candidate.node_a, candidate.node_b)
        if node_id in luna_domain.node_ids
    )


def test_ipv6_boundary_exports_install_over_an_ipv6_seed_and_redistribute() -> None:
    resolved = _reachability_with_ipv6_loopbacks()
    border = _luna_border(resolved)
    vars_for_node = _vars_for(resolved, border)
    seeds = [entry["peer_loopback_ipv6"] for entry in vars_for_node["static_links"]]
    assert seeds and all(seeds)
    ipv6_routes = [r for r in vars_for_node["boundary_static_routes"] if r["family"] == "ipv6"]
    assert ipv6_routes
    assert {route["via"] for route in ipv6_routes} <= set(seeds)
    assert _domain_vars(resolved, border)["redistribute_static"] == ("ipv4", "ipv6")

    conf = _frr_conf(resolved, border)
    for seed in seeds:
        assert f"ipv6 route {seed}/128 " in conf
    assert f"ipv6 route {ipv6_routes[0]['prefix']} {ipv6_routes[0]['via']}" in conf
    router = _router_block(conf, "router isis luna_domain")
    assert " redistribute ipv4 static level-2" in router
    assert " redistribute ipv6 static level-2" in router
    # The unnumbered boundary link borrows the IPv6 loopback, so the peer's
    # seed next hop is on-link.
    loopback = vars_for_node["ipv6_loopback"]
    static_link = vars_for_node["static_links"][0]["name"]
    assert f" ipv6 address {loopback}/128" in _stanza_lines(conf, f"interface {static_link}")


def test_no_ipv6_static_route_renders_where_no_ipv6_loopback_is_declared() -> None:
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml"),
        catalog=shipped_read_view(),
    ).resolved

    for node in resolved.nodes:
        if node.forwarding != "routed":
            continue
        vars_for_node = _vars_for(resolved, node.node_id)
        assert not [r for r in vars_for_node["boundary_static_routes"] if r["family"] == "ipv6"]
        for domain_vars in vars_for_node["domains"]:
            assert "ipv6" not in domain_vars["redistribute_static"]
        assert all(entry["peer_loopback_ipv6"] is None for entry in vars_for_node["static_links"])


def test_ldp_never_runs_on_a_static_boundary_link() -> None:
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml").read_text(
            encoding="utf-8"
        )
    )
    earth = next(domain for domain in raw["routing"]["domains"] if domain["id"] == "earth_domain")
    earth["capabilities"] = {"mpls": {}}
    resolved = resolve_session(raw, source_context=SourceContext(origin="test.frr.ldp"))
    boundary = resolved.routing.boundaries[0]
    earth_domain = next(d for d in resolved.routing_domains if d.domain_id == "earth_domain")
    border = next(
        node_id
        for candidate in resolved.link_candidates
        if candidate.rule_id == boundary.over
        for node_id in (candidate.node_a, candidate.node_b)
        if node_id in earth_domain.node_ids
    )
    vars_for_node = _vars_for(resolved, border)
    static_links = {entry["name"] for entry in vars_for_node["static_links"]}
    igp_links = {
        entry["name"] for domain in vars_for_node["domains"] for entry in domain["interfaces"]
    }
    assert static_links and igp_links

    ldp = {line.strip() for line in _stanza_lines(_frr_conf(resolved, border), "mpls ldp")}

    assert {f"interface {name}" for name in igp_links} <= ldp
    assert not {f"interface {name}" for name in static_links} & ldp


@pytest.mark.parametrize("protocol", ["isis", "ospf"])
def test_an_active_site_lan_runs_the_domain_timers_and_every_lan_carries_its_cost(protocol) -> None:
    resolved = _simple_session_with_bfd(protocol)
    active = _denver_router(resolved)
    passive = next(
        node.node_id
        for node in resolved.nodes
        if node.kind == "ground_station" and not node.node_id.startswith("earth-us-co-denver")
    )
    timers = _domain_vars(resolved, active)

    active_lan = _stanza_lines(_frr_conf(resolved, active), "interface terr0")
    passive_lan = _stanza_lines(_frr_conf(resolved, passive), "interface terr0")

    if protocol == "isis":
        assert f" isis hello-interval {timers['isis_hello_interval']}" in active_lan
        assert f" isis hello-multiplier {timers['isis_hello_multiplier']}" in active_lan
        assert " isis metric 10" in active_lan
        assert " isis passive" in passive_lan
        assert " isis metric 10" in passive_lan
        assert not [line for line in passive_lan if "hello" in line]
    else:
        for version in ("ip ospf", "ipv6 ospf6"):
            assert f" {version} hello-interval {timers['ospf_hello_interval']}" in active_lan
            assert f" {version} dead-interval {timers['ospf_dead_interval']}" in active_lan
            assert f" {version} cost 10" in active_lan
            assert f" {version} passive" in passive_lan
            assert f" {version} cost 10" in passive_lan
        assert not [line for line in passive_lan if "interval" in line]


def test_a_fixed_link_to_a_node_outside_the_domain_carries_no_igp() -> None:
    resolved = _resolved(protocol="ospf", planes=1, slots=4)
    node_id = _first_satellite(resolved)
    [domain] = resolved.routing_domains
    peer_id, interface = next(
        (candidate.node_b, candidate.fixed_interfaces[0])
        for candidate in resolved.link_candidates
        if candidate.kind != "access" and candidate.node_a == node_id
    )
    before = {entry["name"] for entry in _domain_vars(resolved, node_id)["interfaces"]}
    # The same peer, no longer a participant of the domain.
    outside = resolved.model_copy(
        update={
            "routing_domains": (
                domain.model_copy(
                    update={"node_ids": tuple(n for n in domain.node_ids if n != peer_id)}
                ),
            )
        }
    )
    after = {entry["name"] for entry in _domain_vars(outside, node_id)["interfaces"]}

    assert interface in before
    assert before - after == {interface}
    assert not [
        line
        for line in _stanza_lines(_frr_conf(outside, node_id), f"interface {interface}")
        if "ospf" in line
    ]


def _two_protocol_simple_raw(*, terrestrial_detect_multiplier: int = 4) -> dict[str, Any]:
    """earth-leo-simple with IS-IS over the satellites and sites, OSPF over the sites.

    Every site router participates in both domains. Denver wires two routers
    to one LAN. Both domains enable BFD with a detect multiplier of 4 unless
    the terrestrial one is given another.
    """
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = load_configuration_yaml(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml").read_text(encoding="utf-8")
    )
    raw["routing"] = {
        "domains": [
            {
                "id": "orbital",
                "protocol": "isis",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
                "timers": {"bfd": {"enabled": True, "detect_multiplier": 4}},
            },
            {
                "id": "terrestrial",
                "protocol": "ospf",
                "selectors": [{"segment": "ground"}],
                "timers": {
                    "bfd": {"enabled": True, "detect_multiplier": terrestrial_detect_multiplier}
                },
            },
        ]
    }
    return raw


def _two_protocol_simple_session() -> ResolvedSession:
    return resolve_session(
        _two_protocol_simple_raw(), source_context=SourceContext(origin="test.frr.two-domains")
    )


def test_a_router_in_two_domains_runs_each_domain_on_its_own_interfaces() -> None:
    resolved = _two_protocol_simple_session()
    router_id = _denver_router(resolved)
    router = resolved.node_by_id(router_id)
    assert router is not None
    access = {wan.name for wan in router.wan_interfaces}
    assert access
    assert resolved.domain_interfaces(router_id) == {
        "orbital": tuple(sorted(access | {"terr0"})),
        "terrestrial": ("terr0",),
    }

    files = _files(resolved, router_id)
    conf = files["frr.conf"]

    assert {"isisd", "ospfd", "ospf6d", "bfdd"} <= _enabled_daemons(files["daemons"])
    assert _router_block(conf, "router isis orbital")
    assert _router_block(conf, "router ospf")
    # The loopback belongs to both domains.
    lo = _stanza_lines(conf, "interface lo")
    assert " ip router isis orbital" in lo
    assert " ip ospf area 0.0.0.0" in lo
    # Access links run IS-IS only, with the orbital domain's BFD profile.
    for name in access:
        lines = _stanza_lines(conf, f"interface {name}")
        assert " ip router isis orbital" in lines
        assert " isis bfd profile orbital" in lines
        assert not [line for line in lines if "ospf" in line]
    # Both Denver routers share the LAN in both domains, so both run on it.
    lan = _stanza_lines(conf, "interface terr0")
    assert " ip router isis orbital" in lan
    assert " ip ospf area 0.0.0.0" in lan
    assert " ipv6 ospf6 area 0.0.0.0" in lan
    assert " isis bfd profile orbital" in lan
    assert " ip ospf bfd profile terrestrial" in lan
    # One BFD profile per domain, each with its domain's timers.
    profiles = _stanza_lines(conf, "bfd")
    assert profiles.count(" profile orbital") == 1
    assert profiles.count(" profile terrestrial") == 1
    assert profiles[profiles.index(" profile orbital") + 1] == "  detect-multiplier 4"
    assert profiles[profiles.index(" profile terrestrial") + 1] == "  detect-multiplier 4"


def test_domains_sharing_an_interface_must_declare_the_same_bfd_timers() -> None:
    from nodalarc.resolve_session import SessionResolutionError

    raw = _two_protocol_simple_raw(terrestrial_detect_multiplier=5)

    with pytest.raises(
        SessionResolutionError,
        match=r"different BFD timers on shared interfaces: earth-.* terr0 "
        r"\(orbital, terrestrial\)",
    ):
        resolve_session(raw, source_context=SourceContext(origin="test.frr.bfd-conflict"))


def test_a_satellite_outside_the_second_domain_runs_only_its_own() -> None:
    resolved = _two_protocol_simple_session()
    satellite = _first_satellite(resolved)

    files = _files(resolved, satellite)

    assert [domain.domain_id for domain in resolved.routing_domains_for(satellite)] == ["orbital"]
    assert "ospfd" not in _enabled_daemons(files["daemons"])
    assert "ospf" not in files["frr.conf"]
    assert "profile terrestrial" not in files["frr.conf"]
