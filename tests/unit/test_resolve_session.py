# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver acceptance tests for segment-session product grammar."""

import pytest
from nodalarc.models.identity import IdentityMode
from nodalarc.resolve_session import (
    SessionResolutionError,
    resolve_session,
    resolve_session_with_assets,
)
from nodalarc.runtime_support import UnsupportedFeatureError
from pydantic import ValidationError


def _segment_session(**overrides):
    data = {
        "session": {"name": "resolver-demo"},
        "identity": {"mode": "segment_namespaced"},
        "segments": [
            {
                "id": "leo",
                "kind": "constellation",
                "source": "configs/constellations/demo-36.yaml",
                "namespace": "leo",
                "central_body": "earth",
                "tags": ["earth", "leo"],
            },
            {
                "id": "ground",
                "kind": "ground_set",
                "source": "configs/ground-stations/sets/demo.yaml",
                "namespace": "gnd",
                "reference_body": "earth",
                "tags": ["earth", "ground"],
            },
        ],
        "link_rules": [
            {
                "id": "access",
                "kind": "access",
                "endpoints": [
                    {"selector": {"segment": "ground"}, "terminal_role": "ground"},
                    {"selector": {"segment": "leo"}, "terminal_role": "ground"},
                ],
                "topology": {"mode": "visible_candidates"},
            }
        ],
        "simulation": {"candidate_limits": {"max_pairs_per_rule": 252}},
        "orbit": {"propagator": "j2-mean-elements"},
        "scheduling": {
            "ground": {
                "selection_policy": {"name": "highest-elevation", "params": {}},
                "handover_policy": {
                    "name": "hysteresis",
                    "params": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0},
                },
                "handover_mode": "bbm",
                "mbb_overlap_ticks": 0,
                "mbb_reserve": 0,
            }
        },
        "routing": {"protocol": "isis", "extensions": []},
    }
    data.update(overrides)
    return data


def test_old_top_level_session_shape_rejected():
    with pytest.raises(SessionResolutionError, match="old session grammar"):
        resolve_session(
            {
                "session": {"name": "old"},
                "constellation": "configs/constellations/demo-36.yaml",
                "ground_stations": "configs/ground-stations/sets/demo.yaml",
                "orbit": {"propagator": "j2-mean-elements"},
                "routing": {"protocol": "isis"},
            }
        )


def test_segment_session_resolves_namespaced_nodes_and_runtime_projection():
    resolution = resolve_session_with_assets(_segment_session())

    resolved = resolution.resolved
    assert resolved.identity_mode is IdentityMode.SEGMENT_NAMESPACED
    assert "leo-sat-p00s00" in resolved.node_ids()
    assert "gnd-gs-denver" in resolved.node_ids()
    assert resolution.runtime_session.addressing.sat_id_template.startswith("leo-")
    assert resolution.runtime_session.addressing.gs_id_template.startswith("gnd-")
    assert len(resolved.link_rules[0].endpoints[0].node_ids) == 7
    assert len(resolved.link_rules[0].endpoints[1].node_ids) == 36


def test_node_producing_segments_require_namespace():
    data = _segment_session()
    del data["segments"][1]["namespace"]
    with pytest.raises(ValidationError):
        resolve_session(data)


def test_future_structural_grammar_fails_runtime_support_with_typed_error():
    data = _segment_session()
    data["segments"].append(
        {
            "id": "relay",
            "kind": "space_node",
            "namespace": "relay",
            "satellite_type": "meo-geo-rf",
            "node": {
                "id": "relay",
                "state": {
                    "frame": "gcrs",
                    "position_km": [400000.0, 0.0, 0.0],
                    "velocity_km_s": [0.0, 1.0, 0.0],
                },
            },
        }
    )
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        resolve_session(data)
    assert any(feature.value == "space_node" for feature in excinfo.value.features)


def test_terminal_inventory_is_materialized_from_source_satellite_type():
    starlink = resolve_session_with_assets(_segment_session())
    rf_data = _segment_session()
    rf_data["segments"][0]["satellite_type"] = "iridium-next"
    rf = resolve_session_with_assets(rf_data)

    def sat_ground_bandwidths(resolution):
        node = next(n for n in resolution.resolved.nodes if n.kind == "satellite")
        return tuple(
            block.bandwidth_mbps
            for block in node.terminal_inventory
            if block.endpoint_role == "ground"
        )

    assert sat_ground_bandwidths(starlink) != sat_ground_bandwidths(rf)


def test_zero_match_selector_fails_loudly():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][1]["selector"]["planes"] = [99]
    with pytest.raises(SessionResolutionError, match="matched zero nodes"):
        resolve_session(data)


def test_candidate_budget_overflow_fails_before_runtime():
    data = _segment_session()
    data["simulation"]["candidate_limits"] = {"max_pairs_per_rule": 10}
    with pytest.raises(SessionResolutionError, match="max_pairs_per_rule"):
        resolve_session(data)
