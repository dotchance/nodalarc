# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for resolved-session readiness validation."""

from __future__ import annotations

from pathlib import Path

from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.models.segments import GroundScheduling
from nodalarc.resolve_session import resolve_session
from nodalarc.session_validator import build_validation_report, validate_session_readiness

from tests.conftest import build_segment_session_dict

ROOT = Path(__file__).resolve().parents[2]


def _resolved(**overrides):
    raw = build_segment_session_dict(
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
        nodes.append(
            node.model_copy(
                update={"ground_scheduling": scheduling, "terminal_inventory": terminals}
            )
        )
        updated = True
    broken = resolved.model_copy(update={"nodes": tuple(nodes)})

    results = validate_session_readiness(broken, available_node_count=100)

    assert "E021" in _codes(results)
    assert any("requests MBB" in result.message for result in results)


def test_current_ome_runtime_limits_are_reported_before_deploy() -> None:
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
                            "propagator": "sgp4_tle",
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
    assert "sgp4_tle" in messages
    assert "non-Earth" in messages


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
