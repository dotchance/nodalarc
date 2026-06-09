# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contract tests for resolving catalog sessions into runtime truth."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.models.catalog import validate_catalog_document
from nodalarc.resolve_session import (
    SessionResolutionError,
    load_session_resolution_from_file,
    resolve_session,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog" / "nodalarc"
SESSIONS = ROOT / "catalog" / "nodalarc" / "sessions"


def _load(name: str = "earth-leo-simple.yaml") -> dict:
    return yaml.safe_load((SESSIONS / name).read_text(encoding="utf-8"))


def test_all_shipped_catalog_primitives_validate_through_typed_models() -> None:
    paths = sorted(path for path in CATALOG.rglob("*.yaml") if SESSIONS not in path.parents)
    assert paths

    for path in paths:
        validate_catalog_document(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_catalog_primitive_models_reject_extra_fields() -> None:
    raw = yaml.safe_load(
        (CATALOG / "terminals" / "rf" / "rf-ka-leo-access.yaml").read_text(encoding="utf-8")
    )
    raw["terminal"]["driver"] = "frr"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_catalog_document(raw)


# Every shipped session resolves under the production-default runtime-support
# gate, with no per-session opt-outs. If a session is added or its composition
# changes, this table changes with it — silently shipping an unresolvable or
# shrunken session is the failure mode this exists to catch.
SHIPPED_SESSION_SHAPES = {
    "earth-geo-inmarsat.yaml": (8, 16),
    "earth-geo-tdrs.yaml": (10, 24),
    "earth-leo-heo-geo-luna-reachability.yaml": (103, 415),
    "earth-leo-polar.yaml": (39, 108),
    "earth-leo-simple.yaml": (41, 180),
    "earth-leo-walker.yaml": (181, 880),
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
    assert all(
        candidate.interface_a and candidate.interface_b for candidate in resolved.link_candidates
    )
    assert all(candidate.bandwidth_mbps > 0 for candidate in resolved.link_candidates)

    access_candidates = [
        candidate for candidate in resolved.link_candidates if candidate.kind == "access"
    ]
    assert access_candidates
    assert resolved.ground_candidate_satellites_by_gs()
    assert all(
        candidate.interface_a.startswith(("term", "gnd"))
        and candidate.interface_b.startswith(("term", "gnd"))
        for candidate in access_candidates
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
    assert [node.interfaces.lo0.ipv4 for node in satellites[:3] if node.interfaces] == [
        "100.64.0.1/32",
        "100.64.0.2/32",
        "100.64.0.3/32",
    ]
    assert [node.interfaces.lo0.ipv6 for node in satellites[:3] if node.interfaces] == [
        "fd00:6e0::1/128",
        "fd00:6e0::2/128",
        "fd00:6e0::3/128",
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
        SessionResolutionError, match="routing domains must cover every resolved node"
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


def test_placed_ground_node_without_loopback_authority_fails_loudly() -> None:
    raw = _load()
    site = yaml.safe_load(
        (CATALOG / "sites" / "earth" / "us" / "earth-us-hawthorne.yaml").read_text(encoding="utf-8")
    )
    del site["site"]["nodes"][0]["interfaces"]["lo0"]
    raw["segments"][1]["placement"]["from_site_set"] = {
        "site_set": {
            "id": "broken-site-set",
            "sites": [site],
        }
    }

    with pytest.raises(SessionResolutionError, match="invalid catalog object|lo0"):
        resolve_session(raw)


def test_old_top_level_session_keys_are_rejected() -> None:
    raw = _load()
    raw["constellation"] = "configs/constellations/demo.yaml"

    with pytest.raises(SessionResolutionError, match="retired top-level session key"):
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
