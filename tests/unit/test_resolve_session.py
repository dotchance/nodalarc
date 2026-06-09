# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver acceptance tests for the catalog session grammar."""

from __future__ import annotations

from copy import deepcopy

import pytest
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import SessionResolutionError, resolve_session

from tests.conftest import build_segment_session_dict


def _raw_session(**kwargs) -> dict:
    return build_segment_session_dict(
        name=kwargs.pop("name", "resolver-current"),
        constellation=kwargs.pop("constellation", {}),
        ground_stations=kwargs.pop("ground_stations", {"stations": [{}, {}]}),
        **kwargs,
    )


def test_missing_segments_is_rejected_at_catalog_boundary() -> None:
    with pytest.raises(SessionResolutionError, match="requires top-level segments"):
        resolve_session({"session": {"name": "old"}})


def test_retired_top_level_session_keys_are_rejected() -> None:
    raw = _raw_session()
    raw["constellation"] = "configs/constellations/demo-36.yaml"

    with pytest.raises(SessionResolutionError, match="retired top-level session key"):
        resolve_session(raw)


def test_current_catalog_session_resolves_runtime_truth() -> None:
    resolved = resolve_session(
        _raw_session(),
        source_context=SourceContext(origin="test.resolve_session", run_id="run-1"),
    )

    assert resolved.identity_mode.value == "segment_namespaced"
    assert "space-sat-p00s00" in resolved.node_ids()
    assert "earth-test-site-00-router" in resolved.node_ids()
    assert resolved.source_context.run_id == "run-1"
    assert resolved.routing_domains[0].node_ids
    assert resolved.link_candidates
    assert resolved.ground_candidate_satellites_by_gs()


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
    raw = _raw_session()
    raw["link_rules"][1]["topology"] = {"mode": "nearest_visible"}

    with pytest.raises(ValueError, match="dynamic per-tick topology"):
        resolve_session(raw)


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


def test_source_changes_change_resolved_session() -> None:
    raw = _raw_session()
    baseline = resolve_session(raw)
    changed = deepcopy(raw)
    changed["segments"][0]["tags"] = ["changed"]

    updated = resolve_session(changed)

    assert baseline.model_dump(mode="python") != updated.model_dump(mode="python")
    assert all("changed" in node.tags for node in updated.nodes if node.segment_id == "space")
