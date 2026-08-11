# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver acceptance tests for the catalog session grammar."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nodalarc.resolve_session as resolver_module
import pytest
import yaml
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.models.link_rules import LinkRuleConstraints
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.segment_session import RoutingDomain
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.resolve_session import SessionResolutionError
from pydantic import ValidationError

from tests.catalog_session_fixtures import (
    ISS_TLE_LINE_1,
    build_catalog_session_fixture,
    install_tle_space_node_set,
)
from tests.catalog_session_fixtures import (
    resolve_catalog_session as resolve_session,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
BASE_SITE = SHIPPED_ROOT / "sites" / "earth" / "de" / "earth-de-berlin.yaml"
BASE_NODE = SHIPPED_ROOT / "nodes" / "ground" / "starlink-gateway.yaml"


def _raw_session(**kwargs) -> dict:
    return build_catalog_session_fixture(
        name=kwargs.pop("name", "resolver-current"),
        constellation=kwargs.pop("constellation", {}),
        ground_stations=kwargs.pop("ground_stations", {"stations": [{}, {}]}),
        **kwargs,
    )


def _site_node_local_id(raw: Any, index: int = 0) -> str:
    site = raw.read_catalog(raw.site_refs[index])["site"]
    return f"{site['id']}-{site['nodes'][0]['id']}"


def _complete_explicit_plane_mappings() -> list[dict[str, Any]]:
    return [
        {"planes": [0], "area_id": "49.0001"},
        {"planes": [1], "area_id": "49.0002"},
    ]


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _site_document() -> dict[str, Any]:
    document = yaml.safe_load(BASE_SITE.read_text(encoding="utf-8"))
    document["site"]["id"] = "resolver-test-site"
    return document


def _resolve_site_document(
    tmp_path: Path,
    site_document: dict[str, Any],
    *,
    node_document: dict[str, Any] | None = None,
):
    user_root = tmp_path / "user"
    site_ref = "user:sites/resolver-test-site.yaml"
    if node_document is not None:
        node_ref = "user:nodes/resolver-test-node.yaml"
        node_document["node"]["id"] = "resolver-test-node"
        site_document["site"]["nodes"][0]["node"] = node_ref
        _write_yaml(user_root / "nodes" / "resolver-test-node.yaml", node_document)
    _write_yaml(user_root / "sites" / "resolver-test-site.yaml", site_document)
    _write_yaml(
        user_root / "site-sets" / "resolver-test-sites.yaml",
        {
            "site_set": {
                "id": "resolver-test-sites",
                "sites": [site_ref],
            }
        },
    )

    session = yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8"))
    ground_segment = next(segment for segment in session["segments"] if "placement" in segment)
    ground_segment["placement"]["from_site_set"] = "user:site-sets/resolver-test-sites.yaml"
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    return resolve_session(session, catalog_roots=roots)


def _terminal_limits(
    *,
    azimuth_min: float = -180,
    azimuth_max: float = 180,
    elevation_min: float = 0,
    elevation_max: float = 90,
    tracking_rate: float = 3,
) -> dict[str, Any]:
    return {
        "azimuth_deg": {"min": azimuth_min, "max": azimuth_max},
        "elevation_deg": {"min": elevation_min, "max": elevation_max},
        "max_tracking_rate_deg_s": tracking_rate,
    }


def test_missing_segments_is_rejected_at_catalog_boundary() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        resolve_session({"session": {"name": "old"}})


def test_unknown_top_level_session_keys_are_rejected_by_canonical_model() -> None:
    raw = _raw_session()
    raw["constellation"] = "configs/constellations/demo-36.yaml"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        resolve_session(raw)


@pytest.mark.parametrize("selector_kind", ["node", "terminal"])
def test_private_python_selector_spelling_is_rejected(selector_kind: str) -> None:
    raw = _raw_session()
    endpoint = raw["link_rules"][0]["endpoints"][0]
    field = "select" if selector_kind == "node" else "terminal"
    negated = {"tag": "ground"} if selector_kind == "node" else {"role": "access"}
    endpoint[field] = {"not_": negated}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        resolve_session(raw)


def test_current_catalog_session_resolves_runtime_truth() -> None:
    resolved = resolve_session(
        _raw_session(),
        source_context=SourceContext(origin="test.resolve_session", run_id="run-1"),
    )

    assert resolved.identity_mode.value == "segment_namespaced"
    assert "space-sat-p00s00" in resolved.node_ids()
    assert "resolver-current-site-00-router" in resolved.node_ids()
    assert resolved.source_context.run_id == "run-1"
    assert resolved.routing_domains[0].node_ids
    assert resolved.link_candidates
    assert resolved.ground_candidate_satellites_by_gs()


def test_direct_resolver_rejects_referenced_component_identity_mismatch() -> None:
    raw = _raw_session()
    assert raw.space_node_ref is not None
    document = raw.read_catalog(raw.space_node_ref)
    document["node"]["id"] = "wrong-identity"
    raw.write_catalog(raw.space_node_ref, document)

    with pytest.raises(SessionResolutionError, match="must match filename stem"):
        resolve_session(raw)


def _install_second_ground_access_mount(raw: Any) -> None:
    assert raw.ground_node_ref is not None
    node_document = raw.read_catalog(raw.ground_node_ref)
    duplicate = deepcopy(node_document["node"]["terminals"][0])
    duplicate["id"] = "access_backup"
    node_document["node"]["terminals"].append(duplicate)
    raw.write_catalog(raw.ground_node_ref, node_document)

    for site_ref in raw.site_refs:
        site_document = raw.read_catalog(site_ref)
        site_document["site"]["nodes"][0]["terminals"]["access_backup"] = {
            "installed_count": 1,
            "capabilities": {"boresight": {"mode": "local_vertical"}},
        }
        raw.write_catalog(site_ref, site_document)


def test_terminal_selector_rejects_multiple_equal_bandwidth_mounts() -> None:
    raw = _raw_session(ground_stations={"stations": ["a"]})
    _install_second_ground_access_mount(raw)

    with pytest.raises(
        SessionResolutionError,
        match="matches multiple terminal mounts.*select one exact mount",
    ):
        resolve_session(raw)


def test_terminal_selector_accepts_one_explicit_mount() -> None:
    raw = _raw_session(ground_stations={"stations": ["a"]})
    _install_second_ground_access_mount(raw)
    raw["link_rules"][0]["endpoints"][0]["terminal"]["all"].append({"mount": "access"})

    resolved = resolve_session(raw)

    rule = next(item for item in resolved.link_rules if item.rule_id == "ground-access")
    assert rule.endpoints[0].terminal_id == "access"


def test_role_only_terminal_selectors_derive_selected_medium() -> None:
    raw = _raw_session(ground_stations={"stations": ["a"]})
    for endpoint in raw["link_rules"][0]["endpoints"]:
        endpoint["terminal"] = {"role": "access"}

    resolved = resolve_session(raw)

    rule = next(item for item in resolved.link_rules if item.rule_id == "ground-access")
    candidate = next(item for item in resolved.link_candidates if item.rule_id == "ground-access")
    assert tuple(endpoint.terminal_medium for endpoint in rule.endpoints) == ("rf", "rf")
    assert candidate.terminal_medium == "rf"


def test_role_only_terminal_selectors_reject_selected_medium_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_session(ground_stations={"stations": ["a"]})
    for endpoint in raw["link_rules"][0]["endpoints"]:
        endpoint["terminal"] = {"role": "access"}
    assert raw.ground_node_ref is not None
    ground_node = raw.read_catalog(raw.ground_node_ref)
    ground_node["node"]["terminals"][0]["terminal"] = (
        "nodalarc:terminals/optical/optical-low-orbit-isl.yaml"
    )
    raw.write_catalog(raw.ground_node_ref, ground_node)

    def _candidate_generation_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("candidate generation ran before terminal-medium refusal")

    monkeypatch.setattr(
        resolver_module,
        "generate_declared_link_candidates",
        _candidate_generation_must_not_run,
    )

    with pytest.raises(
        SessionResolutionError,
        match=("ground-access.*incompatible terminal media: endpoint 0='optical', endpoint 1='rf'"),
    ):
        resolve_session(raw)


def test_mapped_max_links_covers_selected_nodes_without_candidates() -> None:
    constraints = LinkRuleConstraints.model_validate({"max_links_per_node": {"space": 1}})
    selected = SimpleNamespace(
        node_id="ground-selected",
        segment_id="ground",
        placement_groups=(),
    )
    rule = SimpleNamespace(
        rule_id="explicit-subset",
        constraints=constraints,
        endpoints=(SimpleNamespace(node_ids=(selected.node_id,)),),
    )
    resolved = SimpleNamespace(nodes=(selected,), link_rules=(rule,))

    with pytest.raises(SessionResolutionError, match="has no entry for segment"):
        resolver_module._enforce_link_rule_constraints(resolved, ())


@pytest.mark.parametrize("omit_authored_routing", [False, True])
def test_isis_domains_receive_resolved_spf_defaults(omit_authored_routing: bool) -> None:
    raw = _raw_session(protocol="isis")
    if omit_authored_routing:
        raw.pop("routing")

    resolved = resolve_session(raw)

    assert len(resolved.routing_domains) == 1
    spf = resolved.routing_domains[0].timers.spf
    assert spf.holddown_ms == 2000
    assert spf.time_to_learn_ms == 500


@pytest.mark.parametrize("field", ["holddown_ms", "time_to_learn_ms"])
def test_ospf_rejects_authored_isis_only_spf_fields(field: str) -> None:
    raw = _raw_session(protocol="ospf")
    raw["routing"]["domains"][0]["timers"] = {"spf": {field: 100}}

    with pytest.raises(ValidationError, match="IS-IS-only SPF field"):
        resolve_session(raw)


@pytest.mark.parametrize(
    ("protocol", "area_id", "message"),
    [
        ("ospf", "49.0001", "canonical dotted IPv4 address"),
        ("isis", "0.0.0.0", "canonical lowercase hex dotted area token"),
    ],
)
def test_routing_area_ids_are_validated_for_the_domain_protocol(
    protocol: str,
    area_id: str,
    message: str,
) -> None:
    raw = _raw_session(protocol=protocol)
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "flat",
        "gs_area_id": area_id,
    }

    with pytest.raises(ValidationError, match=message):
        resolve_session(raw)


def test_ospf_per_plane_area_derivation_rejects_more_than_255_planes() -> None:
    raw = _raw_session(
        protocol="ospf",
        constellation={"planes": {"count": 256, "sats_per_plane": 1}},
    )
    raw["routing"]["domains"][0]["area_assignment"] = {"strategy": "per_plane"}

    with pytest.raises(SessionResolutionError, match="area index 256.*limit 255"):
        resolve_session(raw)


def test_isis_derived_area_derivation_rejects_more_than_four_digits() -> None:
    domain = RoutingDomain.model_validate(
        {
            "id": "large-isis",
            "protocol": "isis",
            "selectors": [{"segment": "space"}],
            "area_assignment": {"strategy": "per_plane"},
        }
    )
    nodes = (SimpleNamespace(kind="satellite", plane=9999, node_id="space-sat-p9999s00"),)

    with pytest.raises(SessionResolutionError, match="area index 10000.*limit 9999"):
        resolver_module._validate_area_assignment(domain, nodes)


def test_explicit_area_assignment_can_map_a_ground_only_domain() -> None:
    domain = RoutingDomain.model_validate(
        {
            "id": "ground-ospf",
            "protocol": "ospf",
            "selectors": [{"segment": "ground"}],
            "area_assignment": {
                "strategy": "explicit",
                "assignments": [{"ground_stations": ["site-a-gw1"], "area_id": "0.0.0.1"}],
            },
        }
    )
    nodes = (
        SimpleNamespace(
            kind="ground_station",
            plane=None,
            node_id="site-a-gw1",
            local_node_id="site-a-gw1",
        ),
    )

    resolver_module._validate_area_assignment(domain, nodes)


@pytest.mark.parametrize("protocol", ["bgp", "static"])
def test_non_igp_domains_reject_area_assignment(protocol: str) -> None:
    raw = _raw_session(protocol=protocol)
    raw["routing"]["domains"][0]["area_assignment"] = {"strategy": "flat"}

    with pytest.raises(ValidationError, match="routing areas apply to isis/ospf"):
        resolve_session(raw)


def test_explicit_area_assignment_rejects_unknown_ground_target() -> None:
    raw = _raw_session()
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "assignments": [
            *_complete_explicit_plane_mappings(),
            {"ground_stations": ["absent-site-router"], "area_id": "49.1234"},
        ],
    }

    with pytest.raises(
        SessionResolutionError,
        match="unknown ground station local_node_id value",
    ):
        resolve_session(raw)


def test_explicit_area_assignment_rejects_overlapping_ground_targets() -> None:
    raw = _raw_session()
    target = _site_node_local_id(raw)
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "assignments": [
            *_complete_explicit_plane_mappings(),
            {"ground_stations": "all", "area_id": "49.1000"},
            {"ground_stations": [target], "area_id": "49.2000"},
        ],
    }

    with pytest.raises(SessionResolutionError, match="maps ground station .* more than once"):
        resolve_session(raw)


def test_explicit_area_assignment_requires_every_selected_plane() -> None:
    raw = _raw_session()
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "assignments": [{"planes": [0], "area_id": "49.0001"}],
    }

    with pytest.raises(SessionResolutionError, match=r"no mapping for selected plane\(s\): \[1\]"):
        resolve_session(raw)


def test_explicit_area_assignment_rejects_overlapping_plane_mappings() -> None:
    raw = _raw_session()
    raw["routing"]["domains"][0]["area_assignment"] = {
        "strategy": "explicit",
        "assignments": [
            {"planes": [0], "area_id": "49.0001"},
            {"planes": [0], "area_id": "49.0002"},
            {"planes": [1], "area_id": "49.0003"},
        ],
    }

    with pytest.raises(SessionResolutionError, match="maps plane 0 more than once"):
        resolve_session(raw)


def _configure_split_space_boundary(
    raw: dict[str, Any],
    *,
    left_select: dict[str, Any],
    right_select: dict[str, Any],
) -> None:
    raw["link_rules"][1]["endpoints"][0]["select"] = left_select
    raw["link_rules"][1]["endpoints"][1]["select"] = right_select
    raw["routing"] = {
        "domains": [
            {
                "id": "space-plane-0",
                "protocol": "isis",
                "selectors": [{"plane": 0}],
            },
            {
                "id": "space-plane-1",
                "protocol": "isis",
                "selectors": [{"plane": 1}],
            },
            {
                "id": "ground-domain",
                "protocol": "isis",
                "selectors": [{"segment": "ground"}],
            },
        ],
        "boundaries": [
            {
                "over": "space-isl",
                "adapter": "static_ip",
                "export": [
                    {
                        "from": "space-plane-0",
                        "to": "space-plane-1",
                        "prefixes": {"aggregate_of": "originated"},
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    ("left_select", "right_select", "mixed_endpoint"),
    [
        ({"segment": "space"}, {"segment": "space"}, 0),
        ({"segment": "space"}, {"plane": 0}, 0),
        ({"plane": 0}, {"segment": "space"}, 1),
    ],
)
def test_routing_boundary_rejects_endpoint_domain_mixing(
    left_select: dict[str, Any],
    right_select: dict[str, Any],
    mixed_endpoint: int,
) -> None:
    raw = _raw_session()
    _configure_split_space_boundary(
        raw,
        left_select=left_select,
        right_select=right_select,
    )

    with pytest.raises(
        SessionResolutionError,
        match=rf"endpoint {mixed_endpoint} spans routing domains.*resolve wholly to one domain",
    ):
        resolve_session(raw)


def test_ground_override_must_target_a_site_in_the_selected_site_set() -> None:
    raw = _raw_session()
    raw["segments"][1]["overrides"] = [
        {"match": {"site": "absent-site"}, "tags": ["never-applied"]}
    ]

    with pytest.raises(
        SessionResolutionError,
        match="override targets site id.*absent from its selected site set",
    ):
        resolve_session(raw)


@pytest.mark.parametrize("value", [-0.1, 90.1])
def test_link_endpoint_min_elevation_has_physical_range(value: float) -> None:
    raw = _raw_session()
    raw["link_rules"][0]["endpoints"][0]["min_elevation_deg"] = value

    with pytest.raises(ValidationError):
        resolve_session(raw)


@pytest.mark.parametrize(
    ("rule_index", "endpoint_index", "message"),
    [
        (1, 0, "only on the ground endpoint of an access rule"),
        (0, 1, "for non-ground node"),
    ],
)
def test_link_endpoint_min_elevation_rejects_non_ground_access_semantics(
    rule_index: int,
    endpoint_index: int,
    message: str,
) -> None:
    raw = _raw_session()
    raw["link_rules"][rule_index]["endpoints"][endpoint_index]["min_elevation_deg"] = 10

    with pytest.raises(SessionResolutionError, match=message):
        resolve_session(raw)


def test_duplicate_loopback_addresses_across_sites_are_rejected() -> None:
    raw = _raw_session()
    first_site = raw.read_catalog(raw.site_refs[0])
    second_site = raw.read_catalog(raw.site_refs[1])
    second_site["site"]["nodes"][0]["interfaces"]["lo0"] = deepcopy(
        first_site["site"]["nodes"][0]["interfaces"]["lo0"]
    )
    raw.write_catalog(raw.site_refs[1], second_site)

    with pytest.raises(ValueError, match="duplicate lo0 ipv4 address"):
        resolve_session(raw)


def _add_ground_loopback_assignment(raw: dict[str, Any], ipv4_pool: str) -> None:
    raw["addressing"]["loopbacks"].append(
        {
            "id": "ground-loopbacks-v4",
            "applies_to": {"segment": "ground"},
            "ipv4_pool": ipv4_pool,
            "prefix_length": 32,
            "allocation": "by_node_order",
        }
    )


def test_authored_loopback_inside_selected_assignment_pool_is_preserved() -> None:
    raw = _raw_session(ground_stations={"stations": [{}]})
    _add_ground_loopback_assignment(raw, "10.255.0.0/24")

    resolved = resolve_session(raw)

    ground = next(node for node in resolved.nodes if node.kind == "ground_station")
    assert ground.interfaces is not None
    assert ground.interfaces.lo0.ipv4 == "10.255.0.1/32"


def test_authored_loopback_outside_selected_assignment_pool_is_rejected() -> None:
    raw = _raw_session(ground_stations={"stations": [{}]})
    _add_ground_loopback_assignment(raw, "192.0.2.0/24")

    with pytest.raises(SessionResolutionError, match="authored lo0.*outside allocated pool"):
        resolve_session(raw)


def test_site_terminal_installation_cannot_exceed_node_mount_count(tmp_path: Path) -> None:
    site = _site_document()
    site["site"]["nodes"][0]["terminals"]["access_ka"]["installed_count"] = 65

    with pytest.raises(SessionResolutionError, match="installs 65 terminals.*declares 64"):
        _resolve_site_document(tmp_path, site)


def test_omitted_site_terminal_mount_is_not_installed_or_rule_eligible() -> None:
    raw = _raw_session(ground_stations={"stations": [{}]})
    site_document = raw.read_catalog(raw.site_refs[0])
    site_document["site"]["nodes"][0]["terminals"].pop("access")
    raw.write_catalog(raw.site_refs[0], site_document)

    access_rule = raw["link_rules"].pop(0)
    resolved = resolve_session(raw)
    ground = next(node for node in resolved.nodes if node.kind == "ground_station")
    assert ground.terminal_inventory == ()

    raw["link_rules"].insert(0, access_rule)
    with pytest.raises(
        SessionResolutionError,
        match="terminal selector matched zero compatible mounts",
    ):
        resolve_session(raw)


def test_site_payload_installation_cannot_exceed_node_mount_count(tmp_path: Path) -> None:
    site = _site_document()
    node = yaml.safe_load(BASE_NODE.read_text(encoding="utf-8"))
    node["node"]["payloads"] = [
        {
            "id": "science",
            "payload": "user:payloads/science.yaml",
            "count": 1,
        }
    ]
    site["site"]["nodes"][0]["payloads"] = {"science": {"installed_count": 2}}

    with pytest.raises(SessionResolutionError, match="installs 2 payloads.*declares 1"):
        _resolve_site_document(tmp_path, site, node_document=node)


@pytest.mark.parametrize(
    ("capabilities", "message"),
    [
        (
            {"bandwidth_mbps": {"transmit": 5001, "receive": 5000}},
            "transmit bandwidth override exceeds",
        ),
        (
            {"bandwidth_mbps": {"transmit": 5000, "receive": 5001}},
            "receive bandwidth override exceeds",
        ),
        ({"tracking_capacity": 2}, "tracking_capacity override exceeds"),
        ({"max_range_km": 1251}, "max_range_km override exceeds"),
        (
            {"limits": _terminal_limits(azimuth_min=-181)},
            r"azimuth_deg\.min override widens",
        ),
        (
            {"limits": _terminal_limits(azimuth_max=181)},
            r"azimuth_deg\.max override widens",
        ),
        (
            {"limits": _terminal_limits(elevation_min=-1)},
            r"elevation_deg\.min override widens",
        ),
        (
            {"limits": _terminal_limits(elevation_max=91)},
            r"elevation_deg\.max override widens",
        ),
        (
            {"limits": _terminal_limits(tracking_rate=3.1)},
            "max_tracking_rate_deg_s override exceeds",
        ),
    ],
)
def test_site_terminal_capabilities_may_only_narrow_referenced_terminal(
    tmp_path: Path,
    capabilities: dict[str, Any],
    message: str,
) -> None:
    site = _site_document()
    site["site"]["nodes"][0]["terminals"]["access_ka"]["capabilities"] = capabilities

    with pytest.raises(SessionResolutionError, match=message):
        _resolve_site_document(tmp_path, site)


def test_site_terminal_narrowing_and_boresight_placement_resolve(tmp_path: Path) -> None:
    site = _site_document()
    site["site"]["nodes"][0]["terminals"]["access_ka"]["capabilities"] = {
        "bandwidth_mbps": {"transmit": 4000, "receive": 3500},
        "tracking_capacity": 1,
        "max_range_km": 1000,
        "limits": _terminal_limits(
            azimuth_min=-90,
            azimuth_max=90,
            elevation_min=20,
            elevation_max=80,
            tracking_rate=2,
        ),
        "boresight": {
            "mode": "configured_topocentric",
            "azimuth_deg": 270,
            "elevation_deg": -10,
        },
    }

    resolved = _resolve_site_document(tmp_path, site)
    ground = next(node for node in resolved.nodes if node.kind == "ground_station")
    terminal = ground.terminal_inventory[0]

    assert terminal.bandwidth_mbps == 3500
    assert terminal.tracking_capacity == 1
    assert terminal.max_range_km == 1000
    assert terminal.min_elevation_deg == 20
    assert terminal.tracking_rate_deg_s == 2
    assert terminal.field_of_regard_deg == 180
    assert terminal.boresight == TerminalBoresight(
        mode="configured_topocentric",
        configured_az_deg=270,
        configured_el_deg=-10,
    )


def _space_access_mount(raw: Any) -> tuple[object, dict[str, Any]]:
    assert raw.space_node_ref is not None
    node_document = raw.read_catalog(raw.space_node_ref)
    access_mount = next(
        mount for mount in node_document["node"]["terminals"] if mount["role"] == "access"
    )
    return node_document, access_mount


def test_space_access_boresight_target_body_is_derived_from_placement() -> None:
    resolved = resolve_session(_raw_session())
    satellite = next(node for node in resolved.nodes if node.kind == "satellite")
    access = next(
        block for block in satellite.terminal_inventory if block.endpoint_role == "access"
    )

    assert access.boresight == SatGroundTerminalBoresight(target_body="earth", mode="nadir")
    assert access.field_of_regard_deg == 180


def test_satellite_access_mount_requires_nadir_boresight() -> None:
    raw = _raw_session()
    node_document, access_mount = _space_access_mount(raw)
    access_mount.pop("boresight")
    assert raw.space_node_ref is not None
    raw.write_catalog(raw.space_node_ref, node_document)

    with pytest.raises(SessionResolutionError, match="requires a spacecraft boresight"):
        resolve_session(raw)


def test_satellite_access_mount_rejects_ground_boresight_mode() -> None:
    raw = _raw_session()
    node_document, access_mount = _space_access_mount(raw)
    access_mount["boresight"] = {"mode": "local_vertical"}
    assert raw.space_node_ref is not None
    raw.write_catalog(raw.space_node_ref, node_document)

    with pytest.raises(SessionResolutionError, match="nadir"):
        resolve_session(raw)


def test_ground_access_boresight_belongs_to_site_installation() -> None:
    raw = _raw_session()
    assert raw.ground_node_ref is not None
    node_document = raw.read_catalog(raw.ground_node_ref)
    access_mount = next(
        mount for mount in node_document["node"]["terminals"] if mount["role"] == "access"
    )
    access_mount["boresight"] = {"mode": "nadir"}
    raw.write_catalog(raw.ground_node_ref, node_document)

    with pytest.raises(SessionResolutionError, match="site installation, not the node mount"):
        resolve_session(raw)


def test_ground_access_installation_requires_boresight() -> None:
    raw = _raw_session()
    first_site = raw.read_catalog(raw.site_refs[0])
    installation = next(iter(first_site["site"]["nodes"][0]["terminals"].values()))
    installation["capabilities"].pop("boresight")
    raw.write_catalog(raw.site_refs[0], first_site)

    with pytest.raises(SessionResolutionError, match="requires a site installation boresight"):
        resolve_session(raw)


def test_source_context_must_be_typed() -> None:
    with pytest.raises(SessionResolutionError, match="SourceContext"):
        resolve_session(_raw_session(), source_context={"origin": "dict"})  # type: ignore[arg-type]


def test_candidate_budget_overflow_fails_before_runtime() -> None:
    raw = _raw_session(candidate_limit=1)

    with pytest.raises(SessionResolutionError, match="max_pairs_per_rule"):
        resolve_session(raw)


def test_total_candidate_budget_overflow_fails_before_runtime() -> None:
    raw = _raw_session(candidate_limit=100)
    raw["simulation"]["candidate_limits"]["max_pairs_per_tick"] = 1

    with pytest.raises(SessionResolutionError, match="max_pairs_per_tick"):
        resolve_session(raw)


def test_aggregate_candidate_budget_refuses_many_rules_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_session()
    fixed_rule = raw["link_rules"][1]
    raw["link_rules"] = [{**deepcopy(fixed_rule), "id": f"space-isl-{index}"} for index in range(6)]
    raw["simulation"]["candidate_limits"] = {
        "max_pairs_per_rule": 4,
        "max_pairs_per_tick": 20,
    }

    def _candidate_generation_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("candidate generation ran before aggregate budget refusal")

    monkeypatch.setattr(
        resolver_module,
        "generate_declared_link_candidates",
        _candidate_generation_must_not_run,
    )

    with pytest.raises(
        SessionResolutionError,
        match=(
            "static aggregate candidate upper bound of 24 pairs.*"
            "max_pairs_per_tick=20 before materialization"
        ),
    ):
        resolve_session(raw)


def test_geometry_only_ground_links_require_explicit_acknowledgement() -> None:
    raw = _raw_session()
    raw["simulation"]["ground_link_model"] = "geometry_only"

    with pytest.raises(ValidationError, match="acknowledge_geometry_only"):
        resolve_session(raw)

    raw["simulation"]["acknowledge_geometry_only"] = True
    resolved = resolve_session(raw)

    assert resolved.simulation is not None
    assert resolved.simulation.ground_link_model == "geometry_only"


def test_selector_matching_zero_nodes_fails_before_candidate_generation() -> None:
    raw = _raw_session()
    raw["link_rules"][0]["endpoints"][0]["select"] = {"tag": "missing"}

    with pytest.raises(SessionResolutionError, match="selector matched zero nodes"):
        resolve_session(raw)


def test_terminal_selector_matching_zero_mounts_fails_before_candidate_generation() -> None:
    raw = _raw_session()
    raw["link_rules"][0]["endpoints"][0]["terminal"] = {
        "all": [{"role": "access"}, {"medium": "optical"}]
    }

    with pytest.raises(SessionResolutionError, match="terminal selector matched zero"):
        resolve_session(raw)


def test_disabled_access_link_rule_leaves_no_implicit_ground_candidates() -> None:
    raw = _raw_session()
    raw["link_rules"][0]["enabled"] = False

    resolved = resolve_session(raw)

    assert all(candidate.kind != "access" for candidate in resolved.link_candidates)
    assert resolved.ground_candidate_satellites_by_gs() == {}


def test_explicit_pairs_declare_permission_not_actual_connectivity() -> None:
    raw = _raw_session()
    raw["link_rules"][1]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "sat-p00s00", "b": "sat-p00s01"}],
    }

    resolved = resolve_session(raw)

    isl_candidates = [
        candidate for candidate in resolved.link_candidates if candidate.kind == "isl"
    ]
    assert len(isl_candidates) == 1
    assert isl_candidates[0].pair == ("space-sat-p00s00", "space-sat-p00s01")
    assert isl_candidates[0].topology_mode == "explicit_pairs"


def test_candidate_pair_can_be_owned_by_only_one_enabled_link_rule() -> None:
    raw = _raw_session()
    duplicate_rule = deepcopy(raw["link_rules"][1])
    duplicate_rule["id"] = "space-isl-duplicate"
    raw["link_rules"].append(duplicate_rule)

    with pytest.raises(
        ValueError,
        match="declared by multiple link_rules.*rule ownership must be unique",
    ):
        resolve_session(raw)


def test_explicit_pairs_must_stay_inside_resolved_endpoint_selectors() -> None:
    raw = _raw_session()
    raw["link_rules"][1]["endpoints"][0]["select"] = {"plane": 0}
    raw["link_rules"][1]["endpoints"][1]["select"] = {"plane": 0}
    raw["link_rules"][1]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "sat-p00s00", "b": "sat-p01s00"}],
    }

    with pytest.raises(ValueError, match="outside the resolved endpoint selector sets"):
        resolve_session(raw)


def test_nearest_visible_topology_fails_until_runtime_can_apply_it_per_tick() -> None:
    from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

    raw = _raw_session()
    raw["link_rules"][1]["topology"] = {"mode": "nearest_visible"}

    with pytest.raises(UnsupportedFeatureError) as error:
        resolve_session(raw)
    assert any(
        feature.category == FeatureCategory.LINK_TOPOLOGY and feature.value == "nearest_visible"
        for feature in error.value.features
    )


def test_runtime_node_id_length_fails_before_kubernetes() -> None:
    raw = _raw_session()
    long_segment = "space-" + ("x" * 80)
    raw["segments"][0]["id"] = long_segment
    raw["link_rules"][0]["endpoints"][1]["select"] = {"segment": long_segment}
    raw["link_rules"][1]["endpoints"][0]["select"] = {"segment": long_segment}
    raw["link_rules"][1]["endpoints"][1]["select"] = {"segment": long_segment}
    raw["routing"]["domains"][0]["selectors"] = [
        {"any": [{"segment": long_segment}, {"segment": "ground"}]}
    ]

    with pytest.raises(ValueError, match="runtime node_id"):
        resolve_session(raw)


def test_omitted_routing_requires_at_least_one_router() -> None:
    # Router-ness derives from the workload profile, never the wiring class:
    # a session whose profiles render no routing has zero routers.
    raw = _raw_session()
    assert raw.space_node_ref is not None
    assert raw.ground_node_ref is not None
    for node_ref in (raw.space_node_ref, raw.ground_node_ref):
        node_document = raw.read_catalog(node_ref)
        node_document["node"]["profile"] = "nodalarc:profiles/linux-host.yaml"
        raw.write_catalog(node_ref, node_document)
    raw.pop("routing")

    with pytest.raises(
        SessionResolutionError,
        match="declares no routing and resolves zero routers",
    ):
        resolve_session(raw)


def test_source_changes_change_resolved_session() -> None:
    raw = _raw_session()
    baseline = resolve_session(raw)
    changed = deepcopy(raw)
    changed["segments"][0]["tags"] = ["changed"]

    updated = resolve_session(changed)

    assert baseline.model_dump(mode="python") != updated.model_dump(mode="python")
    assert all("changed" in node.tags for node in updated.nodes if node.segment_id == "space")


def test_crtbp_propagator_is_future_gated_with_typed_reason() -> None:
    """NRHO/halo orbits are three-body trajectories; until a CR3BP propagator
    lands, sessions referencing them must fail with a typed UnsupportedFeature
    — never fly a plausible-looking but physically false Kepler ellipse."""
    from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

    raw = _raw_session(orbit_propagator="crtbp")
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        resolve_session(raw)
    features = excinfo.value.features
    assert any(f.category == FeatureCategory.PROPAGATOR and f.value == "crtbp" for f in features)
    assert any("three-body" in (f.support_note or "") for f in features)


def test_canonical_sgp4_space_node_set_materializes_exact_runtime_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_session(name="resolver-sgp4", ground_stations={"stations": ["a"]})
    install_tle_space_node_set(raw)
    calls = 0
    propagate = resolver_module.propagate_sgp4_tle

    def counted_propagation(*args, **kwargs):
        nonlocal calls
        calls += 1
        return propagate(*args, **kwargs)

    monkeypatch.setattr(resolver_module, "propagate_sgp4_tle", counted_propagation)

    resolved = resolve_session(raw)
    satellites = [node for node in resolved.nodes if node.kind == "satellite"]

    assert calls == len(satellites) == 2
    assert [node.slot for node in satellites] == [0, 1]
    assert all(node.plane == 0 for node in satellites)
    assert all(node.orbit is not None for node in satellites)
    iss = next(node for node in satellites if node.local_node_id == "iss")
    assert iss.orbit is not None
    assert iss.orbit.propagator == "sgp4_tle"
    assert iss.orbit.tle_line_1 == ISS_TLE_LINE_1
    assert iss.orbit.norad_id == 25544
    assert resolved.link_candidates


def test_sgp4_tle_placement_is_earth_only() -> None:
    raw = _raw_session(name="resolver-sgp4-luna", ground_stations={"stations": ["a"]})
    install_tle_space_node_set(raw, body_ref="nodalarc:bodies/luna.yaml")

    with pytest.raises(SessionResolutionError, match="SGP4/TLE placement is Earth-only"):
        resolve_session(raw)


def test_shipped_nrho_constellation_is_future_gated() -> None:
    """The shipped Gateway-class NRHO content is authored grammar the runtime
    cannot fly yet; composing it into a session must reject loudly."""
    from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

    raw = _raw_session(
        constellation="nodalarc:constellations/luna/nrho/luna-nrho-relay-1.yaml",
    )
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        resolve_session(raw)
    assert any(
        f.category == FeatureCategory.PROPAGATOR and f.value == "crtbp"
        for f in excinfo.value.features
    )
