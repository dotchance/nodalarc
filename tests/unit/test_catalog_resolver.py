# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contract tests for resolving catalog sessions into runtime truth."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.resolve_session import (
    SessionResolutionError,
    _derive_link_label,
    load_session_resolution_from_file,
    resolve_session,
)
from nodalarc.session_validator import validate_session_readiness
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog" / "nodalarc"
SESSIONS = ROOT / "catalog" / "nodalarc" / "sessions"
ACTIVE_SESSION_FIXTURES = ROOT / "tests" / "fixtures" / "sessions"


def _load(name: str = "earth-leo-simple.yaml") -> dict:
    return load_configuration_yaml((SESSIONS / name).read_text(encoding="utf-8"))


def test_all_shipped_catalog_primitives_validate_through_typed_models() -> None:
    paths = sorted(path for path in CATALOG.rglob("*.yaml") if SESSIONS not in path.parents)
    assert paths

    for path in paths:
        ref = CatalogRef(f"nodalarc:{path.relative_to(CATALOG).as_posix()}")
        validate_referenced_configuration_document(
            ref,
            load_configuration_yaml(path.read_text(encoding="utf-8")),
        )


def test_catalog_primitive_models_reject_extra_fields() -> None:
    raw = load_configuration_yaml(
        (CATALOG / "terminals" / "rf" / "rf-ka-leo-access.yaml").read_text(encoding="utf-8")
    )
    raw["terminal"]["driver"] = "frr"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_referenced_configuration_document(
            "nodalarc:terminals/rf/rf-ka-leo-access.yaml",
            raw,
        )


@pytest.mark.parametrize("direction", ["transmit", "receive"])
def test_terminal_bandwidth_must_be_usable_in_both_directions(direction: str) -> None:
    raw = load_configuration_yaml(
        (CATALOG / "terminals" / "rf" / "rf-ka-leo-access.yaml").read_text(encoding="utf-8")
    )
    raw["terminal"]["bandwidth_mbps"][direction] = 0

    with pytest.raises(ValueError, match="greater than 0"):
        validate_referenced_configuration_document(
            "nodalarc:terminals/rf/rf-ka-leo-access.yaml",
            raw,
        )


def test_constellation_phasing_mode_matches_the_declared_plane_shape() -> None:
    ring = load_configuration_yaml(
        (CATALOG / "constellations" / "earth" / "leo" / "earth-leo-ring-36.yaml").read_text(
            encoding="utf-8"
        )
    )
    ring["constellation"]["planes"]["count"] = 2
    with pytest.raises(ValueError, match="requires exactly one orbital plane"):
        validate_referenced_configuration_document(
            "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
            ring,
        )

    walker = load_configuration_yaml(
        (
            CATALOG / "constellations" / "earth" / "leo" / "earth-leo-walker-delta-176.yaml"
        ).read_text(encoding="utf-8")
    )
    walker["constellation"]["planes"]["count"] = 1
    with pytest.raises(ValueError, match="requires at least two planes"):
        validate_referenced_configuration_document(
            "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
            walker,
        )


# Every shipped session resolves under the production-default runtime-support
# gate, with no per-session opt-outs. If a session is added or its composition
# changes, this table changes with it — silently shipping an unresolvable or
# shrunken session is the failure mode this exists to catch.
SHIPPED_SESSION_SHAPES = {
    "earth-geo-inmarsat.yaml": (8, 16),
    "earth-geo-tdrs.yaml": (10, 24),
    "earth-leo-heo-geo-luna-reachability.yaml": (132, 587),
    # 80/153: a full Iridium-class 66-satellite shell (continuous
    # mid-latitude coverage), GEO ring, lunar ELFO relays, one Earth QUIC
    # lab and one lunar QUIC lab, each pairing a routed gateway with a
    # host-forwarding endpoint that joins no routing domain.
    # 86/357: the quic sky with the lunar relay replaced by a DTN relay
    # vehicle carrying a uD3TN store-and-forward host on its payload bus,
    # plus one Earth and one lunar DTN endpoint lab beside the QUIC labs.
    "earth-luna-dtn.yaml": (86, 357),
    # Same sky and counts as earth-luna-dtn; only the relay composition
    # differs (storage-backed custody contact manager).
    "earth-luna-dtn-custody.yaml": (86, 357),
    "earth-luna-quic.yaml": (81, 290),
    # 43/246: four feeder-class bridge sites added (Djibouti, Singapore,
    # Cape Town, Tokyo — authored ranges cover the 780 km shell at the
    # declared mask) and the ISL rule pinned to the 30 physically visible
    # cross-plane pairs — at this shell's density the ground segment
    # carries inter-chain connectivity (BBM bridge gateways hold steady
    # multi-links; MBB's reserve would forbid bridging).
    "earth-leo-polar.yaml": (45, 282),
    "earth-leo-simple.yaml": (41, 180),
    # 1056 = 880 + 176: the ISL rule moved from nearest_n (176 degree-capped
    # pairs, a disconnected graph) to the explicit 352-pair grid.
    "earth-leo-walker.yaml": (181, 1056),
    "earth-meo-gps.yaml": (31, 120),
}


def test_every_shipped_catalog_session_resolves_to_runtime_truth() -> None:
    paths = sorted(SESSIONS.glob("*.yaml"))
    assert [path.name for path in paths] == sorted(SHIPPED_SESSION_SHAPES)

    for path in paths:
        resolution = load_session_resolution_from_file(path)
        resolved = resolution.resolved
        expected_nodes, expected_candidates = SHIPPED_SESSION_SHAPES[path.name]
        assert len(resolved.nodes) == expected_nodes, path.name
        assert len(resolved.link_candidates) == expected_candidates, path.name

        # Loopback identity: unique per family across the whole session.
        for family in ("ipv4", "ipv6"):
            seen: dict[str, str] = {}
            for node in resolved.nodes:
                if node.interfaces is None:
                    continue
                value = getattr(node.interfaces.lo0, family)
                if value is None:
                    continue
                address = value.split("/")[0]
                assert address not in seen, (
                    f"{path.name}: duplicate lo0 {family} {address} on "
                    f"{seen[address]} and {node.node_id}"
                )
                seen[address] = node.node_id

        sr_domains = [
            domain
            for domain in resolved.routing_domains
            if "segment_routing" in domain.capabilities
        ]
        assert {domain.domain_id for domain in sr_domains} == {
            block.domain_id for block in resolved.sid_blocks
        }
        assert all(0 < sid <= 8000 for sid in resolved.sid_index_by_node_id().values())
        assert all(
            node.interfaces is not None and node.interfaces.lo0 is not None
            for node in resolved.nodes
            if node.forwarding == "routed"
        )
        assert all(node.orbit is not None for node in resolved.nodes if node.kind == "satellite")
        assert all(
            node.surface_position is not None
            for node in resolved.nodes
            if node.kind == "ground_station"
        )


def test_catalog_resolver_materializes_runtime_domains_and_link_candidates() -> None:
    resolved = resolve_session(_load("earth-leo-heo-geo-luna-reachability.yaml"))

    assert {(domain.domain_id, domain.protocol) for domain in resolved.routing_domains} == {
        ("earth_domain", "isis"),
        ("luna_domain", "isis"),
    }
    assert resolved.link_candidates
    assert all(candidate.node_a != candidate.node_b for candidate in resolved.link_candidates)
    fixed_candidates = [
        candidate for candidate in resolved.link_candidates if candidate.kind != "access"
    ]
    assert fixed_candidates
    assert all(candidate.interface_a and candidate.interface_b for candidate in fixed_candidates)
    assert all(candidate.bandwidth_mbps > 0 for candidate in resolved.link_candidates)

    access_candidates = [
        candidate for candidate in resolved.link_candidates if candidate.kind == "access"
    ]
    assert access_candidates
    assert resolved.ground_candidate_satellites_by_gs()
    assert all(candidate.interface_a is None for candidate in access_candidates)
    assert all(candidate.interface_b is None for candidate in access_candidates)
    assert set(resolved.link_interface_map()) == {candidate.pair for candidate in fixed_candidates}


def test_link_classes_follow_resolved_body_relationships() -> None:
    resolved = resolve_session(_load("earth-leo-heo-geo-luna-reachability.yaml"))
    rules = {rule.rule_id: rule for rule in resolved.link_rules}
    nodes = {node.node_id: node for node in resolved.nodes}

    assert rules["leo_a_access"].kind == "access"
    for rule_id in ("leo_to_heo", "meo_to_geo", "heo_to_geo"):
        rule = rules[rule_id]
        assert rule.kind == "isl"
        assert rule.endpoints[0].segment_id != rule.endpoints[1].segment_id
        assert {
            nodes[node_id].central_body
            for endpoint in rule.endpoints
            for node_id in endpoint.node_ids
        } == {"earth"}

    inter_body = rules["geo_to_luna"]
    assert inter_body.kind == "inter_body"
    assert {
        nodes[node_id].central_body
        for endpoint in inter_body.endpoints
        for node_id in endpoint.node_ids
    } == {"earth", "luna"}
    assert not {rule.kind for rule in resolved.link_rules} & {"relay", "backbone"}


def test_link_class_rejects_mixed_same_body_and_cross_body_pairs() -> None:
    resolved = resolve_session(_load("earth-leo-heo-geo-luna-reachability.yaml"))
    rule = next(rule for rule in resolved.link_rules if rule.rule_id == "geo_to_luna")
    nodes = {node.node_id: node for node in resolved.nodes}
    earth_nodes = tuple(nodes[node_id] for node_id in rule.endpoints[0].node_ids)
    luna_nodes = tuple(nodes[node_id] for node_id in rule.endpoints[1].node_ids)

    with pytest.raises(SessionResolutionError, match="mixes same-body and cross-body"):
        _derive_link_label(
            "mixed_body_rule",
            list(rule.endpoints),
            [earth_nodes, earth_nodes + luna_nodes],
        )


def test_catalog_resolver_preserves_eccentric_orbit_facts() -> None:
    resolved = resolve_session(_load("earth-leo-heo-geo-luna-reachability.yaml"))

    heo_nodes = [node for node in resolved.nodes if node.segment_id == "heo_relay"]
    assert heo_nodes
    eccentricities = {round(node.orbit.eccentricity, 3) for node in heo_nodes if node.orbit}
    assert eccentricities == {0.737}


def test_generated_space_nodes_get_deterministic_runtime_loopbacks() -> None:
    resolved = resolve_session(_load())

    satellites = [node for node in resolved.nodes if node.kind == "satellite"]
    assert satellites
    assert all(node.interfaces is not None for node in satellites)
    # Ground environments allocate from the shared pool first (sorted by
    # node id in the segment-allocation pass), so the first satellites
    # follow the five simple-session ground routers.
    assert [node.interfaces.lo0.ipv4 for node in satellites[:3] if node.interfaces] == [
        "100.64.0.6/32",
        "100.64.0.7/32",
        "100.64.0.8/32",
    ]
    assert [node.interfaces.lo0.ipv6 for node in satellites[:3] if node.interfaces] == [
        "fd00:6e0::6/128",
        "fd00:6e0::7/128",
        "fd00:6e0::8/128",
    ]


def test_session_without_routing_gets_one_default_runtime_domain() -> None:
    resolved = resolve_session(_load())

    assert len(resolved.routing_domains) == 1
    domain = resolved.routing_domains[0]
    assert domain.domain_id == "default_domain"
    assert domain.protocol == "isis"
    assert set(domain.node_ids) == {node.node_id for node in resolved.nodes}


def test_session_without_dispatch_gets_resolved_runtime_dispatch_truth() -> None:
    raw = _load()
    raw.pop("dispatch", None)

    resolved = resolve_session(raw)

    assert resolved.dispatch is not None
    assert resolved.dispatch.latency_authority == "ome"
    assert resolved.dispatch.max_latency_age_ticks == 3


def test_explicit_routing_domains_must_cover_every_node() -> None:
    raw = _load()
    raw["routing"] = {
        "domains": [
            {
                "id": "leo_domain",
                "protocol": "isis",
                "selectors": [{"segment": "leo"}],
            }
        ]
    }

    with pytest.raises(
        SessionResolutionError, match="routing domains must cover every router"
    ):
        resolve_session(raw)


def test_explicit_routing_domains_must_be_disjoint() -> None:
    raw = _load()
    raw["routing"] = {
        "domains": [
            {
                "id": "all_domain",
                "protocol": "isis",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
            },
            {
                "id": "leo_domain",
                "protocol": "isis",
                "selectors": [{"segment": "leo"}],
            },
        ]
    }

    with pytest.raises(SessionResolutionError, match="routing domains must be disjoint"):
        resolve_session(raw)


def test_placed_ground_nodes_get_deterministic_allocated_loopbacks(tmp_path: Path) -> None:
    # Loopback authority is the resolver everywhere; a placed ground node
    # always receives a deterministic allocated lo0.
    raw = _load()
    site = load_configuration_yaml(
        (CATALOG / "sites" / "earth" / "us" / "earth-us-hawthorne.yaml").read_text(encoding="utf-8")
    )

    user_root = tmp_path / "user"
    site_path = user_root / "sites" / "earth" / "us" / "earth-us-hawthorne.yaml"
    site_path.parent.mkdir(parents=True)
    site_path.write_text(yaml.safe_dump(site, sort_keys=False), encoding="utf-8")
    site_set_path = user_root / "site-sets" / "broken-site-set.yaml"
    site_set_path.parent.mkdir(parents=True)
    site_set_path.write_text(
        yaml.safe_dump(
            {
                "site_set": {
                    "id": "broken-site-set",
                    "sites": ["user:sites/earth/us/earth-us-hawthorne.yaml"],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    raw["segments"][1]["placement"]["from_site_set"] = "user:site-sets/broken-site-set.yaml"
    roots = CatalogRoots.from_catalog_root(CATALOG, user_root=user_root)

    resolution = resolve_session(raw, catalog_roots=roots)
    ground = [
        node
        for node in resolution.nodes
        if node.kind == "ground_station" and node.namespace == "earth-us-hawthorne"
    ]
    assert ground
    for node in ground:
        assert node.interfaces is not None
        assert node.interfaces.lo0.ipv4 is not None
        assert node.interfaces.lo0.ipv6 is not None

    again = resolve_session(raw, catalog_roots=roots)
    assert {
        node.node_id: node.interfaces.lo0.ipv4
        for node in resolution.nodes
        if node.interfaces is not None
    } == {
        node.node_id: node.interfaces.lo0.ipv4
        for node in again.nodes
        if node.interfaces is not None
    }


def test_unknown_top_level_session_keys_are_rejected_by_canonical_model() -> None:
    raw = _load()
    raw["constellation"] = "configs/constellations/demo.yaml"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        resolve_session(raw)


def test_selector_matching_zero_nodes_fails_loudly() -> None:
    raw = _load("earth-leo-heo-geo-luna-reachability.yaml")
    raw["link_rules"][0]["endpoints"][0]["select"] = {"tag": "does_not_exist"}

    with pytest.raises(SessionResolutionError, match="selector matched zero nodes"):
        resolve_session(raw)


def test_terminal_selector_matching_zero_mounts_fails_loudly() -> None:
    raw = _load("earth-leo-heo-geo-luna-reachability.yaml")
    raw["link_rules"][0]["endpoints"][0]["terminal"] = {"role": "crosslink"}

    with pytest.raises(SessionResolutionError, match="terminal selector matched zero"):
        resolve_session(raw)


def test_terminal_install_count_drives_derived_unnumbered_wan_interfaces() -> None:
    resolved = resolve_session(_load("earth-leo-heo-geo-luna-reachability.yaml"))
    ground = next(node for node in resolved.nodes if node.kind == "ground_station")
    access = next(block for block in ground.terminal_inventory if block.terminal_id == "access_ka")

    assert access.count == len(ground.wan_interfaces)
    assert {iface.borrows for iface in ground.wan_interfaces} == {"lo0"}
    assert {iface.name for iface in ground.wan_interfaces} == {
        f"term{index}" for index in range(access.count)
    }


def test_segment_apply_originated_prefixes_merge_with_site_node_intent() -> None:
    raw = _load("earth-leo-heo-geo-luna-reachability.yaml")
    resolved = resolve_session(raw)

    leo_a_ground = [
        node
        for node in resolved.nodes
        if node.segment_id == "leo_a_ground" and node.originated_prefixes is not None
    ]
    assert leo_a_ground
    for node in leo_a_ground:
        assert "0.0.0.0/0" in node.originated_prefixes.ipv4
        assert any(prefix != "0.0.0.0/0" for prefix in node.originated_prefixes.ipv4)


def test_catalog_source_change_changes_resolved_session() -> None:
    raw = _load("earth-leo-heo-geo-luna-reachability.yaml")
    baseline = resolve_session(raw)
    changed = deepcopy(raw)
    changed["segments"][0]["tags"] = ["changed"]

    updated = resolve_session(changed)

    assert baseline.model_dump(mode="python") != updated.model_dump(mode="python")
    assert all("changed" in node.tags for node in updated.nodes if node.segment_id == "leo_a")


def test_every_active_session_fixture_resolves_through_shared_authority() -> None:
    paths = sorted(ACTIVE_SESSION_FIXTURES.glob("*.yaml"))
    assert paths

    for path in paths:
        resolution = load_session_resolution_from_file(path)
        assert resolution.resolved.nodes, path.name
        assert resolution.resolved.link_candidates, path.name
        findings = validate_session_readiness(
            resolution.resolved,
            available_node_count=3,
        )
        errors = [finding.message for finding in findings if finding.level == "error"]
        assert errors == [], f"{path.name}: {errors}"
