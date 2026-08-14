# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for resolved-session readiness validation."""

from __future__ import annotations

from pathlib import Path

from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.models.segments import GroundScheduling
from nodalarc.session_validator import build_validation_report, validate_session_readiness

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    install_tle_space_node_set,
)
from tests.catalog_session_fixtures import (
    resolve_catalog_session as resolve_session,
)

ROOT = Path(__file__).resolve().parents[2]


def _resolved(**overrides):
    raw = build_catalog_session_fixture(
        name=overrides.pop("name", "validator-catalog-session"),
        constellation=overrides.pop(
            "constellation",
            {"planes": {"count": 2, "sats_per_plane": 2}},
        ),
        ground_stations=overrides.pop("ground_stations", {"stations": ["a", "b"]}),
        **overrides,
    )
    return resolve_session(raw)


def _codes(results):
    return {result.code for result in results}


def test_validator_reads_resolved_session_without_old_config_imports() -> None:
    source = (ROOT / "lib" / "nodalarc" / "session_validator.py").read_text(encoding="utf-8")

    assert "nodalarc.models.session" not in source
    assert "nodalarc.models.ground_station" not in source
    assert "nodalarc.models.constellation" not in source
    assert "expand_constellation" not in source


def test_e021_counts_only_rule_selected_interfaces() -> None:
    """One selected interface plus two unselected mounted interfaces under
    MBB reserve 1 must fail readiness: OME builds runtime inventory from
    rule-selected mounts only, so mounted-but-unselected interfaces are
    capacity no allocation can grant. Counting them validated sessions
    that starved at runtime."""
    from copy import deepcopy

    import yaml
    from nodalarc.configuration_yaml import load_configuration_yaml

    raw = build_catalog_session_fixture(
        name="validator-selected-capacity",
        constellation={"planes": {"count": 2, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
        scheduling={"handover_mode": "mbb", "mbb_overlap_ticks": 2, "mbb_reserve": 1},
    )

    node_path = raw.roots.user_root / raw.ground_node_ref.relative_path
    node_doc = load_configuration_yaml(node_path.read_text(encoding="utf-8"))
    mounts = node_doc["node"]["terminals"]
    second = deepcopy(mounts[0])
    second["id"] = "access2"
    second["tags"] = ["access2"]
    mounts.append(second)
    node_path.write_text(yaml.safe_dump(node_doc), encoding="utf-8")

    site_path = raw.roots.user_root / raw.site_refs[0].relative_path
    site_doc = load_configuration_yaml(site_path.read_text(encoding="utf-8"))
    installs = site_doc["site"]["nodes"][0]["terminals"]
    installs["access"]["installed_count"] = 1
    installs["access2"] = {
        "installed_count": 2,
        "capabilities": {"boresight": {"mode": "local_vertical"}},
    }
    site_path.write_text(yaml.safe_dump(site_doc), encoding="utf-8")

    access_rule = next(rule for rule in raw["link_rules"] if rule["id"] == "ground-access")
    access_rule["endpoints"][0]["terminal"] = {
        "all": [{"role": "access"}, {"medium": "rf"}, {"mount": "access"}]
    }

    resolved = resolve_session(raw)
    results = validate_session_readiness(resolved, available_node_count=100)
    e021 = [result for result in results if result.code == "E021"]
    assert e021, "selected capacity 1 under reserve 1 must fail readiness"
    assert "capacity 1" in e021[0].message

    # Control: raising the SELECTED mount to two interfaces satisfies
    # reserve 1; the unselected mounts still contribute nothing.
    installs["access"]["installed_count"] = 2
    site_path.write_text(yaml.safe_dump(site_doc), encoding="utf-8")
    resolved = resolve_session(raw)
    results = validate_session_readiness(resolved, available_node_count=100)
    assert not [result for result in results if result.code == "E021"]


def test_resolved_catalog_session_validates_cleanly() -> None:
    resolved = _resolved()

    results = validate_session_readiness(resolved, available_node_count=100)

    assert results == []


def test_old_session_shape_is_not_accepted() -> None:
    old_shape = {"session": {"name": "old"}, "constellation": "configs/constellations/demo.yaml"}

    try:
        validate_session_readiness(old_shape)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "ResolvedSession" in str(exc)
    else:
        raise AssertionError("old session shape was accepted")


def test_enabled_link_rule_with_no_candidates_is_error() -> None:
    resolved = _resolved()
    first_rule_id = resolved.link_rules[0].rule_id
    broken = resolved.model_copy(
        update={
            "link_candidates": tuple(
                candidate
                for candidate in resolved.link_candidates
                if candidate.rule_id != first_rule_id
            )
        }
    )

    results = validate_session_readiness(broken, available_node_count=100)

    assert "E003" in _codes(results)
    assert any(first_rule_id in result.message for result in results)


def test_routing_domain_member_without_internal_candidate_is_error() -> None:
    resolved = _resolved(ground_stations={"stations": ["a", "b", "c"]})
    ground_ids = [node.node_id for node in resolved.nodes if node.kind == "ground_station"]
    isolated_domain = ResolvedRoutingDomain(
        domain_id="ground_only_domain",
        protocol="isis",
        node_ids=tuple(ground_ids[:2]),
        capabilities=(),
    )
    broken = resolved.model_copy(
        update={"routing_domains": resolved.routing_domains + (isolated_domain,)}
    )

    results = validate_session_readiness(broken, available_node_count=100)

    assert "E003" in _codes(results)
    assert any("ground_only_domain" in result.message for result in results)


def test_ground_mbb_requires_access_capacity_for_reserve() -> None:
    resolved = _resolved()
    nodes = []
    updated = False
    for node in resolved.nodes:
        if node.kind != "ground_station" or updated:
            nodes.append(node)
            continue
        scheduling = GroundScheduling(
            selection_policy=node.ground_scheduling.selection_policy,
            handover_policy=node.ground_scheduling.handover_policy,
            handover_mode="mbb",
            mbb_overlap_ticks=1,
            mbb_reserve=1,
            handover_concurrency=node.ground_scheduling.handover_concurrency,
            ranking_order=node.ground_scheduling.ranking_order,
        )
        terminals = tuple(
            block.model_copy(update={"count": 1, "tracking_capacity": 1})
            if block.endpoint_role == "access"
            else block
            for block in node.terminal_inventory
        )
        access_terminal_ids = {
            block.terminal_id
            for block in node.terminal_inventory
            if block.endpoint_role == "access"
        }
        retained_access_interfaces: set[str] = set()
        wan_interfaces = []
        for interface in node.wan_interfaces:
            if interface.terminal_id in access_terminal_ids:
                if interface.terminal_id in retained_access_interfaces:
                    continue
                retained_access_interfaces.add(interface.terminal_id)
            wan_interfaces.append(interface)
        nodes.append(
            node.model_copy(
                update={
                    "ground_scheduling": scheduling,
                    "terminal_inventory": terminals,
                    "wan_interfaces": tuple(wan_interfaces),
                }
            )
        )
        updated = True
    broken = resolved.model_copy(update={"nodes": tuple(nodes)})

    results = validate_session_readiness(broken, available_node_count=100)

    assert "E021" in _codes(results)
    assert any("requests MBB" in result.message for result in results)


def test_missing_non_earth_ephemeris_is_reported_before_deploy() -> None:
    resolved = _resolved()
    nodes = []
    updated = False
    for node in resolved.nodes:
        if node.kind != "satellite" or updated:
            nodes.append(node)
            continue
        nodes.append(
            node.model_copy(
                update={
                    "central_body": "luna",
                    "orbit": node.orbit.model_copy(
                        update={
                            "central_body": "luna",
                            "orbit_id": "luna-eccentric-test",
                            "eccentricity": 0.25,
                        }
                    ),
                }
            )
        )
        updated = True
    unsupported = resolved.model_copy(update={"nodes": tuple(nodes)})

    results = validate_session_readiness(unsupported, available_node_count=100)

    assert "E020" in _codes(results)
    messages = "\n".join(result.message for result in results)
    assert "non-Earth" in messages


def test_complete_sgp4_tle_session_has_no_ome_readiness_error() -> None:
    raw = build_catalog_session_fixture(
        name="validator-sgp4",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    install_tle_space_node_set(raw)
    resolved = resolve_session(raw)

    results = validate_session_readiness(resolved, available_node_count=100)

    assert "E020" not in _codes(results)


def test_available_node_count_warning_is_non_blocking() -> None:
    resolved = _resolved(constellation={"planes": {"count": 3, "sats_per_plane": 3}})

    results = validate_session_readiness(resolved, available_node_count=1)
    report = build_validation_report(resolved, results)

    assert "W004" in _codes(results)
    assert report.status == "valid"
    assert report.dispatchable is True
    assert report.warnings


def test_validation_report_blocks_on_errors() -> None:
    resolved = _resolved()
    broken = resolved.model_copy(update={"link_candidates": ()})

    results = validate_session_readiness(broken, available_node_count=100)
    report = build_validation_report(broken, results)

    assert report.status == "invalid"
    assert report.dispatchable is False
    assert report.errors
    assert report.effective_config["session"]["name"] == "validator-catalog-session"


def test_mixed_score_scales_with_selection_score_ranking_fail_the_gate(tmp_path) -> None:
    """E022 belongs at the deploy gate — a session whose ranking compares
    incompatible raw scores must never reach an OME startup crash."""
    from nodalarc.session_validator import validate_session_readiness

    raw = build_catalog_session_fixture(
        name="mixed-scales",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a", "b"]},
    )
    site_document = raw.read_catalog(raw.site_refs[1])
    site_document["site"]["nodes"][0]["scheduling"] = {
        "selection_policy": {"longest_remaining_pass": {"lookahead_horizon_ticks": 600}},
    }
    raw.write_catalog(raw.site_refs[1], site_document)
    resolved = resolve_session(raw)
    errors = [
        r
        for r in validate_session_readiness(resolved, available_node_count=4)
        if r.level == "error" and r.code == "E022"
    ]
    assert errors and "incompatible score scales" in errors[0].message

    # per_gs_rank arbitration makes the same mix valid.
    raw["segments"][1]["apply"]["scheduling"]["ranking_order"] = [
        "service_priority",
        "per_gs_rank",
        "lex_pair",
    ]
    site_document["site"]["nodes"][0]["scheduling"]["ranking_order"] = [
        "service_priority",
        "per_gs_rank",
        "lex_pair",
    ]
    raw.write_catalog(raw.site_refs[1], site_document)
    resolved = resolve_session(raw)
    errors = [
        r
        for r in validate_session_readiness(resolved, available_node_count=4)
        if r.level == "error" and r.code == "E022"
    ]
    assert errors == []
