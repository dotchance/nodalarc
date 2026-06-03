# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver acceptance tests for segment-session product grammar."""

from pathlib import Path

import pytest
import yaml
from nodalarc.link_metadata import build_link_metadata_maps
from nodalarc.models.identity import IdentityMode
from nodalarc.models.session import resolve_session_epoch
from nodalarc.resolve_session import (
    SessionResolutionError,
    load_session_resolution_from_file,
    resolve_session,
    resolve_session_with_assets,
)
from nodalarc.runtime_support import UnsupportedFeatureError
from ome.event_stream import (
    build_session_ephemeris,
    build_step_context,
    compute_step,
    precompute_timeline_window,
)
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
    assert resolution.addressing.sat_id(0, 0) == "leo-sat-p00s00"
    assert resolution.addressing.gs_id("denver") == "gnd-gs-denver"
    assert len(resolved.link_rules[0].endpoints[0].node_ids) == 7
    assert len(resolved.link_rules[0].endpoints[1].node_ids) == 36
    assert len(resolution.declared_candidates) == 252


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
            "kind": "lagrange_point",
            "namespace": "relay",
            "satellite_type": "cislunar-relay-rf",
            "frame": {
                "primary_body": "earth",
                "secondary_body": "luna",
                "point": "L1",
                "ephemeris": {"model": "lagrange_approximation"},
            },
        }
    )
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        resolve_session(data)
    assert any(feature.value == "lagrange_point" for feature in excinfo.value.features)


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
    first_sat_ifaces = sorted(
        assignment.interface for node_id, assignment in rf.neighbors if node_id == "leo-sat-p00s00"
    )
    assert len(first_sat_ifaces) == len(set(first_sat_ifaces))
    assert set(first_sat_ifaces).issubset({"isl0", "isl1", "isl2", "isl3"})


def test_internal_isl_assignment_respects_source_terminal_roles():
    data = _segment_session()
    data["segments"][0]["source"] = "configs/constellations/iridium-66.yaml"
    data["simulation"]["candidate_limits"] = {"max_pairs_per_rule": 500}

    resolution = resolve_session_with_assets(data)
    by_type: dict[str, set[str]] = {}
    for _node_id, assignment in resolution.neighbors:
        by_type.setdefault(assignment.link_type, set()).add(assignment.interface)

    assert by_type["intra_plane_isl"] <= {"isl0", "isl1"}
    assert by_type["cross_plane_isl"] <= {"isl2", "isl3"}
    assert by_type["intra_plane_isl"]
    assert by_type["cross_plane_isl"]


def _site_terminal_session():
    data = _segment_session()
    data["segments"][1]["source"] = {
        "default_min_elevation_deg": 10,
        "ground_sites": [
            {
                "id": "santiago",
                "display_name": "Santiago Gateway",
                "lat_deg": -33.45,
                "lon_deg": -70.66,
                "tags": ["gateway"],
                "nodes": [
                    {
                        "id": "leo-router",
                        "tags": ["leo-access"],
                        "handover_mode": "mbb",
                        "mbb_overlap_ticks": 2,
                        "mbb_reserve": 1,
                        "terminals": [
                            {
                                "id": "leo-ka",
                                "type": "rf",
                                "band": "Ka",
                                "count": 2,
                                "bandwidth_mbps": 1200,
                                "tracking_capacity": 2,
                                "max_range_km": 2500,
                                "field_of_regard_deg": 140,
                                "max_tracking_rate_deg_s": 2.0,
                                "boresight": {"mode": "local_vertical"},
                                "tags": ["leo", "ka"],
                            }
                        ],
                    },
                    {
                        "id": "geo-router",
                        "tags": ["geo-gateway"],
                        "handover_mode": "bbm",
                        "mbb_overlap_ticks": 0,
                        "mbb_reserve": 0,
                        "terminals": [
                            {
                                "id": "geo-c",
                                "type": "rf",
                                "band": "C",
                                "count": 1,
                                "bandwidth_mbps": 250,
                                "tracking_capacity": 1,
                                "max_range_km": 45000,
                                "field_of_regard_deg": 80,
                                "max_tracking_rate_deg_s": 0.05,
                                "boresight": {"mode": "local_vertical"},
                                "tags": ["geo", "c-band"],
                            }
                        ],
                    },
                ],
            }
        ],
    }
    data["link_rules"][0]["endpoints"][0] = {
        "selector": {"segment": "ground", "node_tags": ["leo-access"]},
        "terminal_role": "ground",
        "terminal_medium": "rf",
        "terminal_id": "leo-ka",
    }
    data["simulation"]["candidate_limits"] = {"max_pairs_per_rule": 36}
    return data


def test_ground_site_expands_to_independent_ground_nodes_with_terminal_blocks():
    resolution = resolve_session_with_assets(_site_terminal_session())
    ground_nodes = {
        node.local_node_id: node
        for node in resolution.resolved.nodes
        if node.kind == "ground_station"
    }

    assert set(ground_nodes) == {"gs-santiago-leo-router", "gs-santiago-geo-router"}
    leo = ground_nodes["gs-santiago-leo-router"]
    geo = ground_nodes["gs-santiago-geo-router"]

    assert leo.node_id == "gnd-gs-santiago-leo-router"
    assert geo.node_id == "gnd-gs-santiago-geo-router"
    assert leo.tags == (
        "earth",
        "ground",
        "gateway",
        "leo-access",
        "leo",
        "ka",
        "santiago",
        "leo-router",
        "leo-ka",
    )
    assert leo.ground_scheduling.handover_mode == "mbb"
    assert leo.ground_scheduling.mbb_overlap_ticks == 2
    assert geo.ground_scheduling.handover_mode == "bbm"
    assert leo.terminal_inventory[0].source_terminal_id == "leo-ka"
    assert leo.terminal_inventory[0].count == 2
    assert leo.terminal_inventory[0].tracking_capacity == 2
    assert leo.terminal_inventory[0].bandwidth_mbps == 1200
    assert geo.terminal_inventory[0].source_terminal_id == "geo-c"
    assert geo.terminal_inventory[0].bandwidth_mbps == 250
    assert len(resolution.declared_candidates) == 36
    assert len(resolution.ground_candidate_satellites_by_gs["gnd-gs-santiago-leo-router"]) == 36
    assert resolution.ground_candidate_satellites_by_gs["gnd-gs-santiago-geo-router"] == ()


def test_link_rule_terminal_id_mismatch_fails_before_runtime():
    data = _site_terminal_session()
    data["link_rules"][0]["endpoints"][0]["terminal_id"] = "geo-c"

    with pytest.raises(SessionResolutionError, match="terminal_id='geo-c'"):
        resolve_session_with_assets(data)


def test_resolved_ground_nodes_carry_effective_station_handover_policy():
    data = _segment_session()
    data.pop("scheduling")
    data["segments"][1]["scheduling"] = {
        "selection_policy": {"name": "highest-elevation", "params": {}},
        "handover_policy": {
            "name": "hysteresis",
            "params": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0},
        },
    }
    data["segments"][1]["source"] = {
        "default_terminals": [
            {
                "type": "rf",
                "count": 1,
                "bandwidth_mbps": 1000,
                "tracking_capacity": 1,
                "max_range_km": 2000,
                "field_of_regard_deg": 120,
                "max_tracking_rate_deg_s": 1.5,
                "boresight": {"mode": "local_vertical"},
            }
        ],
        "default_handover_mode": "mbb",
        "default_mbb_overlap_ticks": 3,
        "default_mbb_reserve": 1,
        "stations": [
            {"name": "single", "lat_deg": 10.0, "lon_deg": 20.0},
            {
                "name": "multi",
                "lat_deg": 11.0,
                "lon_deg": 21.0,
                "terminals": [
                    {
                        "type": "rf",
                        "count": 2,
                        "bandwidth_mbps": 1000,
                        "tracking_capacity": 1,
                        "max_range_km": 2000,
                        "field_of_regard_deg": 120,
                        "max_tracking_rate_deg_s": 1.5,
                        "boresight": {"mode": "local_vertical"},
                    }
                ],
            },
        ],
    }
    data["simulation"]["candidate_limits"] = {"max_pairs_per_rule": 72}

    resolution = resolve_session_with_assets(data)
    modes = {
        node.local_node_id: node.ground_scheduling.handover_mode
        for node in resolution.resolved.nodes
        if node.kind == "ground_station"
    }

    assert modes == {"gs-single": "bbm", "gs-multi": "mbb"}


def test_segment_session_without_ground_scheduling_fails_loud():
    data = _segment_session()
    data.pop("scheduling")

    with pytest.raises(SessionResolutionError, match="must declare scheduling"):
        resolve_session(data)


def test_ground_segment_handover_override_beats_source_default():
    data = _segment_session()
    data.pop("scheduling")
    data["segments"][1]["source"] = "configs/ground-stations/sets/demo-mbb.yaml"
    data["segments"][1]["scheduling"] = {
        "selection_policy": {"name": "highest-elevation", "params": {}},
        "handover_policy": {
            "name": "hysteresis",
            "params": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0},
        },
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 0,
        "mbb_reserve": 0,
    }

    resolution = resolve_session_with_assets(data)
    modes = {
        node.local_node_id: node.ground_scheduling.handover_mode
        for node in resolution.resolved.nodes
        if node.kind == "ground_station"
    }

    assert set(modes.values()) == {"bbm"}


def test_ground_segment_rejects_allocator_wide_scheduling_fields():
    data = _segment_session()
    data["segments"][1]["scheduling"] = {"successor_abort_policy": "hard_release"}

    with pytest.raises(SessionResolutionError, match="allocator-wide scheduling field"):
        resolve_session_with_assets(data)


def test_cross_body_access_link_rule_rejected_before_ome_wrong_frame_visibility():
    data = yaml.safe_load(Path("configs/sessions/earth-luna-gateway-site.yaml").read_text())
    data["link_rules"][0]["id"] = "bad-earth-ground-to-luna-access"
    data["link_rules"][0]["endpoints"][1]["selector"] = {"segment": "luna-relay"}

    with pytest.raises(SessionResolutionError, match="crosses bodies"):
        resolve_session_with_assets(data)


def test_access_terminal_id_rejected_when_node_has_unselected_compatible_terminal_blocks():
    data = _site_terminal_session()
    terminals = data["segments"][1]["source"]["ground_sites"][0]["nodes"][0]["terminals"]
    backup = dict(terminals[0])
    backup["id"] = "backup-ka"
    terminals.append(backup)

    with pytest.raises(SessionResolutionError, match="terminal-block-aware"):
        resolve_session_with_assets(data)


def test_direct_resolver_source_context_requires_typed_source_context():
    with pytest.raises(SessionResolutionError, match="source_context must be a SourceContext"):
        resolve_session(_segment_session(), source_context="test")  # type: ignore[arg-type]


def test_m1_rejects_access_terminal_media_mismatch_at_resolve_boundary():
    data = _segment_session()
    data["segments"][0]["satellite_type"] = "generic-4isl"
    with pytest.raises(SessionResolutionError, match="no compatible 'ground' terminal medium"):
        resolve_session(data)


def test_selector_matching_zero_nodes_fails_before_candidate_generation():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][1]["selector"]["planes"] = [99]
    with pytest.raises(SessionResolutionError, match="matched zero nodes"):
        resolve_session(data)


def test_candidate_budget_overflow_fails_before_runtime():
    data = _segment_session()
    data["simulation"]["candidate_limits"] = {"max_pairs_per_rule": 10}
    with pytest.raises(SessionResolutionError, match="max_pairs_per_rule"):
        resolve_session(data)


def test_narrowed_link_rule_selector_limits_declared_candidate_universe():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][1]["selector"]["slots"] = [0]

    resolution = resolve_session_with_assets(data)

    assert len(resolution.declared_candidates) == 7
    assert set(resolution.ground_candidate_satellites_by_gs) == {
        "gnd-gs-ashburn",
        "gnd-gs-denver",
        "gnd-gs-frankfurt",
        "gnd-gs-hartebeesthoek",
        "gnd-gs-hawthorne",
        "gnd-gs-singapore",
        "gnd-gs-tokyo",
    }
    assert set(resolution.ground_candidate_satellites_by_gs.values()) == {("leo-sat-p00s00",)}


def test_disabled_access_link_rule_leaves_no_implicit_ground_candidates():
    data = _segment_session()
    data["link_rules"][0]["enabled"] = False
    with pytest.raises(SessionResolutionError, match="no declared access candidates"):
        resolve_session(data)


def test_nearest_n_topology_limits_declared_candidates_by_static_physical_rank():
    data = _segment_session()
    data["link_rules"][0]["topology"] = {"mode": "nearest_n", "n": 1}

    resolution = resolve_session_with_assets(data)
    degree: dict[str, int] = {}
    for candidate in resolution.declared_candidates:
        for node_id in candidate.pair:
            degree[node_id] = degree.get(node_id, 0) + 1

    assert 0 < len(resolution.declared_candidates) < 252
    assert max(degree.values()) == 1
    assert all(
        len(sat_ids) >= 1 for sat_ids in resolution.ground_candidate_satellites_by_gs.values()
    )


def test_nearest_visible_topology_fails_until_ome_can_apply_it_per_tick():
    data = _segment_session()
    data["link_rules"][0]["topology"] = {"mode": "nearest_visible"}
    with pytest.raises(SessionResolutionError, match="nearest_visible"):
        resolve_session(data)


def test_m1_rejects_terminal_role_runtime_cannot_honor():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["terminal_role"] = "isl"
    with pytest.raises(SessionResolutionError, match="terminal_role='isl'"):
        resolve_session(data)


def test_terminal_medium_filter_is_carried_on_declared_candidates():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["terminal_medium"] = "rf"

    resolution = resolve_session_with_assets(data)

    assert len(resolution.declared_candidates) == 252
    assert {candidate.terminal_medium for candidate in resolution.declared_candidates} == {"rf"}


def test_explicit_pairs_declare_permission_not_actual_connectivity():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["selector"]["names"] = ["denver"]
    data["link_rules"][0]["endpoints"][1]["selector"]["slots"] = [0]
    data["link_rules"][0]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "gs-denver", "b": "sat-P00S00"}],
    }

    resolution = resolve_session_with_assets(data)

    assert tuple(candidate.pair for candidate in resolution.declared_candidates) == (
        ("gnd-gs-denver", "leo-sat-p00s00"),
    )
    assert resolution.declared_candidates[0].rule_id == "access"
    assert resolution.ground_candidate_satellites_by_gs["gnd-gs-denver"] == ("leo-sat-p00s00",)
    assert resolution.ground_candidate_satellites_by_gs["gnd-gs-ashburn"] == ()


def test_explicit_pairs_must_stay_inside_resolved_endpoint_selectors():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["selector"]["names"] = ["denver"]
    data["link_rules"][0]["endpoints"][1]["selector"]["slots"] = [0]
    data["link_rules"][0]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "gs-ashburn", "b": "sat-P00S00"}],
    }

    with pytest.raises(SessionResolutionError, match="outside the resolved endpoint selector sets"):
        resolve_session(data)


def test_declared_candidate_rule_metadata_feeds_link_metadata_maps():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["selector"]["names"] = ["denver"]
    data["link_rules"][0]["endpoints"][1]["selector"]["slots"] = [0]
    data["link_rules"][0]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "gs-denver", "b": "sat-P00S00"}],
    }
    resolution = resolve_session_with_assets(data)
    metadata = build_link_metadata_maps(
        resolution.runtime_session,
        resolution.addressing,
        constellation=resolution.runtime_constellation,
        satellites=resolution.satellites,
        gs_file=resolution.primary_ground_set.config,
        neighbors=resolution.neighbors,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
        declared_candidates=resolution.declared_candidates,
    )

    pair = ("gnd-gs-denver", "leo-sat-p00s00")
    assert metadata.rule_map[pair].link_rule_id == "access"
    assert metadata.rule_map[pair].topology_mode == "explicit_pairs"
    assert metadata.rule_map[pair].endpoint_segments == ("ground", "leo")


def test_declared_access_candidates_are_the_only_pairs_ome_evaluates():
    data = _segment_session()
    data["link_rules"][0]["endpoints"][0]["selector"]["names"] = ["denver"]
    data["link_rules"][0]["endpoints"][1]["selector"]["slots"] = [0]
    data["link_rules"][0]["topology"] = {
        "mode": "explicit_pairs",
        "pairs": [{"a": "gs-denver", "b": "sat-P00S00"}],
    }
    resolution = resolve_session_with_assets(data)

    ctx = build_step_context(
        satellites=list(resolution.satellites),
        addressing=resolution.addressing,
        gs_file=resolution.primary_ground_set.config,
        neighbors=resolution.neighbors,
        propagator_id=resolution.runtime_session.orbit.propagator,
        ground_scheduling=resolution.runtime_session.scheduling.ground,
        ground_link_model=resolution.runtime_session.simulation.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
    )

    result = compute_step(
        ctx,
        1704067200.0,
        0,
        resolution.runtime_session.time.step_seconds,
        0.0,
        {},
        {},
    )

    assert set(result.ground_decisions) == {("gnd-gs-denver", "leo-sat-p00s00")}
    assert result.associations == {}


def test_static_max_links_per_node_constraint_is_enforced_at_resolve_time():
    data = _segment_session()
    data["link_rules"][0]["constraints"] = {"max_links_per_node": 1}
    with pytest.raises(SessionResolutionError, match="exceeding max_links_per_node=1"):
        resolve_session(data)


def test_dynamic_link_rule_constraints_fail_until_ome_consumes_them():
    data = _segment_session()
    data["link_rules"][0]["constraints"] = {"max_range_km": 1000.0}
    with pytest.raises(SessionResolutionError, match="unsupported runtime constraint"):
        resolve_session(data)


def test_m1_rejects_protocol_boundary_until_runtime_honors_it():
    data = _segment_session()
    data["link_rules"][0]["protocol_boundary"] = {"enabled": True, "adapter": "bgp"}
    with pytest.raises(UnsupportedFeatureError, match="protocol_adapter"):
        resolve_session(data)


def test_m1_rejects_multiple_link_rules_until_runtime_honors_them():
    data = _segment_session()
    data["link_rules"].append(dict(data["link_rules"][0], id="second-access"))
    with pytest.raises(SessionResolutionError, match="declared by multiple link_rules"):
        resolve_session(data)


def test_m1_rejects_non_access_link_rule_until_runtime_honors_it():
    data = _segment_session()
    data["link_rules"][0]["kind"] = "relay"
    with pytest.raises(SessionResolutionError, match="must connect satellite nodes"):
        resolve_session(data)


def test_runtime_node_id_length_fails_before_kubernetes():
    data = _segment_session()
    data["segments"][0]["namespace"] = "n" * 60
    with pytest.raises(SessionResolutionError, match="Kubernetes label value limit"):
        resolve_session(data)


def test_earth_leo_meo_geo_demo_resolves_stitched_candidate_graph():
    resolution = load_session_resolution_from_file(
        Path("configs/sessions/earth-leo-meo-geo.yaml"),
        origin="test.resolve_session",
    )

    assert {node.segment_id for node in resolution.resolved.nodes} == {
        "leo",
        "meo",
        "geo",
        "ground",
    }
    assert {"leo-sat-p00s00", "meo-sat-p00s00", "geo-sat-p00s00"}.issubset(
        set(resolution.resolved.node_ids())
    )
    by_rule: dict[str, int] = {}
    for candidate in resolution.declared_candidates:
        by_rule[candidate.rule_id] = by_rule.get(candidate.rule_id, 0) + 1

    assert by_rule == {
        "ground-to-leo": 216,
        "ground-to-meo": 144,
        "ground-to-geo": 48,
        "leo-to-meo-relay-candidates": 24,
        "meo-to-geo-relay-candidates": 8,
    }
    assert all(
        len(sat_ids) == 68 for sat_ids in resolution.ground_candidate_satellites_by_gs.values()
    )
    neighbor_types: dict[str, int] = {}
    for _node_id, assignment in resolution.neighbors:
        neighbor_types[assignment.link_type] = neighbor_types.get(assignment.link_type, 0) + 1
    assert neighbor_types["intra_plane_isl"] == 72
    assert neighbor_types["link_rule:leo-to-meo-relay-candidates"] == 48
    assert neighbor_types["link_rule:meo-to-geo-relay-candidates"] == 16
    leo_meo_pairs = [
        candidate.pair
        for candidate in resolution.declared_candidates
        if candidate.rule_id == "leo-to-meo-relay-candidates"
    ]
    assert not any(node_id.startswith("geo-") for pair in leo_meo_pairs for node_id in pair)


def test_earth_luna_relay_demo_resolves_and_computes_common_frame_relay():
    resolution = load_session_resolution_from_file(
        Path("configs/sessions/earth-luna-relay.yaml"),
        origin="test.resolve_session",
    )

    assert resolution.active_bodies == frozenset({"earth", "luna"})
    assert resolution.body_ephemeris is not None
    assert {"earth-relay", "luna-relay", "lunar-ground"} == {
        node.segment_id for node in resolution.resolved.nodes
    }
    assert "earth-relay-gateway" in resolution.resolved.node_ids()
    assert "luna-sat-p00s00" in resolution.resolved.node_ids()
    assert any(
        assignment.link_type == "static_ip:earth-luna-static-relay"
        for _node_id, assignment in resolution.neighbors
    )

    node_metadata = {
        node.node_id: {
            "segment_id": node.segment_id,
            "local_node_id": node.local_node_id,
            "namespace": node.namespace,
            "tags": tuple(node.tags),
            "reference_body": node.reference_body or node.central_body or "earth",
            "frame_id": node.frame_id,
        }
        for node in resolution.resolved.nodes
    }
    ctx = build_step_context(
        satellites=list(resolution.satellites),
        addressing=resolution.addressing,
        gs_file=resolution.primary_ground_set.config,
        neighbors=resolution.neighbors,
        propagator_id=resolution.runtime_session.orbit.propagator,
        ground_scheduling=resolution.runtime_session.scheduling.ground,
        ground_link_model=resolution.runtime_session.simulation.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
        node_metadata=node_metadata,
        body_ephemeris=resolution.body_ephemeris,
        active_bodies=resolution.active_bodies,
    )

    epoch_unix = 1704067200.0
    ephemeris = build_session_ephemeris(ctx, epoch_unix, epoch_id=0)
    assert set(ephemeris.body_frames) == {"earth", "luna"}
    assert ephemeris.nodes["luna-sat-p00s00"].reference_body == "luna"
    assert ephemeris.nodes["earth-relay-gateway"].reference_body == "earth"

    result = compute_step(
        ctx,
        epoch_unix,
        0,
        resolution.runtime_session.time.step_seconds,
        0.0,
        {},
        {},
    )

    static_pairs = {
        candidate.pair
        for candidate in resolution.declared_candidates
        if candidate.rule_id == "earth-luna-static-relay"
    }
    assert len(static_pairs) == 1
    static_pair = next(iter(static_pairs))
    feasibility = result.isl_feasibility.get(static_pair) or result.isl_feasibility.get(
        (static_pair[1], static_pair[0])
    )
    assert feasibility is not None
    assert feasibility.feasible
    assert feasibility.reject_reason == "ok"
    assert feasibility.range_km > 300_000
    assert feasibility.orbital_one_way_ms > 1_000

    window = precompute_timeline_window(
        satellites=list(resolution.satellites),
        addressing=resolution.addressing,
        gs_file=resolution.primary_ground_set.config,
        neighbors=resolution.neighbors,
        epoch_unix=epoch_unix,
        duration_s=resolution.runtime_session.time.step_seconds,
        propagator_id=resolution.runtime_session.orbit.propagator,
        step_seconds=resolution.runtime_session.time.step_seconds,
        ground_scheduling=resolution.runtime_session.scheduling.ground,
        ground_link_model=resolution.runtime_session.simulation.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
        body_ephemeris=resolution.body_ephemeris,
        active_bodies=resolution.active_bodies,
    )
    assert window.isl_state


def test_earth_luna_gateway_site_demo_resolves_ground_site_primitives():
    resolution = load_session_resolution_from_file(
        Path("configs/sessions/earth-luna-gateway-site.yaml"),
        origin="test.resolve_session",
    )

    assert {node.segment_id for node in resolution.resolved.nodes} == {
        "leo",
        "meo",
        "geo",
        "earth-cislunar-relay",
        "luna-relay",
        "earth-site",
        "lunar-site",
    }

    by_rule: dict[str, int] = {}
    for candidate in resolution.declared_candidates:
        by_rule[candidate.rule_id] = by_rule.get(candidate.rule_id, 0) + 1
    assert by_rule == {
        "santiago-leo-access": 36,
        "santiago-geo-access": 8,
        "santiago-earth-cislunar-gateway-access": 1,
        "artemis-lunar-access": 8,
        "leo-to-meo-backbone": 24,
        "meo-to-geo-backbone": 8,
        "geo-to-earth-cislunar-relay": 1,
        "earth-to-luna-static-relay": 1,
    }

    earth_leo = resolution.resolved.node_by_id("earth-site-gs-santiago-leo-router")
    earth_geo = resolution.resolved.node_by_id("earth-site-gs-santiago-geo-gateway-router")
    earth_cislunar = resolution.resolved.node_by_id(
        "earth-site-gs-santiago-cislunar-gateway-router"
    )
    lunar = resolution.resolved.node_by_id("lunar-site-gs-artemis-surface-router")
    assert earth_leo is not None
    assert earth_geo is not None
    assert earth_cislunar is not None
    assert lunar is not None

    assert earth_leo.local_node_id == "gs-santiago-leo-router"
    assert earth_leo.segment_id == "earth-site"
    assert earth_leo.reference_body == "earth"
    assert earth_leo.ground_scheduling.handover_mode == "mbb"
    assert earth_leo.terminal_inventory[0].source_terminal_id == "leo-ka"
    assert earth_leo.terminal_inventory[0].count == 2
    assert earth_leo.terminal_inventory[0].bandwidth_mbps == 1200

    assert earth_geo.segment_id == "earth-site"
    assert earth_geo.ground_scheduling.handover_mode == "bbm"
    assert earth_geo.terminal_inventory[0].source_terminal_id == "geo-c"
    assert earth_geo.terminal_inventory[0].bandwidth_mbps == 250

    assert earth_cislunar.segment_id == "earth-site"
    assert earth_cislunar.ground_scheduling.handover_mode == "bbm"
    assert earth_cislunar.terminal_inventory[0].source_terminal_id == "cislunar-c"
    assert earth_cislunar.terminal_inventory[0].bandwidth_mbps == 250

    assert lunar.segment_id == "lunar-site"
    assert lunar.reference_body == "luna"
    assert lunar.terminal_inventory[0].source_terminal_id == "lunar-s-band"

    static_neighbors = [
        assignment
        for _node_id, assignment in resolution.neighbors
        if assignment.link_type == "static_ip:earth-to-luna-static-relay"
    ]
    assert len(static_neighbors) == 2

    node_metadata = {
        node.node_id: {
            "segment_id": node.segment_id,
            "local_node_id": node.local_node_id,
            "namespace": node.namespace,
            "tags": tuple(node.tags),
            "reference_body": node.reference_body or node.central_body or "earth",
            "frame_id": node.frame_id,
        }
        for node in resolution.resolved.nodes
    }
    ctx = build_step_context(
        satellites=list(resolution.satellites),
        addressing=resolution.addressing,
        gs_file=resolution.primary_ground_set.config,
        neighbors=resolution.neighbors,
        propagator_id=resolution.runtime_session.orbit.propagator,
        ground_scheduling=resolution.runtime_session.scheduling.ground,
        ground_link_model=resolution.runtime_session.simulation.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
        node_metadata=node_metadata,
        body_ephemeris=resolution.body_ephemeris,
        active_bodies=resolution.active_bodies,
    )
    result = compute_step(
        ctx,
        resolve_session_epoch(resolution.runtime_session.time),
        0,
        resolution.runtime_session.time.step_seconds,
        0.0,
        {},
        {},
    )
    static_pair = next(
        candidate.pair
        for candidate in resolution.declared_candidates
        if candidate.rule_id == "earth-to-luna-static-relay"
    )
    feasibility = result.isl_feasibility.get(static_pair) or result.isl_feasibility.get(
        (static_pair[1], static_pair[0])
    )
    assert feasibility is not None
    assert feasibility.feasible
    assert feasibility.reject_reason == "ok"
    assert feasibility.range_km > 300_000
    assert feasibility.orbital_one_way_ms > 1_000

    earth_gateway_pair = ("earth-relay-gateway", earth_cislunar.node_id)
    assert earth_gateway_pair in result.ground_allocation.associations

    geo_gateway_pair = ("earth-site-gs-santiago-geo-gateway-router", "geo-sat-p00s02")
    assert geo_gateway_pair in result.ground_allocation.associations


def test_non_earth_session_requires_ephemeris_manifest():
    data = yaml.safe_load(Path("configs/sessions/earth-luna-relay.yaml").read_text())
    data.pop("ephemeris")

    with pytest.raises(SessionResolutionError, match="declares no ephemeris manifest"):
        resolve_session_with_assets(data)


def test_ephemeris_manifest_must_cover_session_epoch():
    data = yaml.safe_load(Path("configs/sessions/earth-luna-relay.yaml").read_text())
    kernel = data["ephemeris"]["kernels"][0]
    kernel["coverage_start"] = "1850-01-01T00:00:00Z"
    kernel["coverage_end"] = "1900-01-01T00:00:00Z"

    with pytest.raises(SessionResolutionError, match="does not cover session epoch"):
        resolve_session_with_assets(data)


def test_m3_ephemeris_rejects_multi_kernel_stack_until_supported():
    data = yaml.safe_load(Path("configs/sessions/earth-luna-relay.yaml").read_text())
    second = dict(data["ephemeris"]["kernels"][0])
    second["id"] = "second-kernel"
    data["ephemeris"]["kernels"].append(second)

    with pytest.raises(SessionResolutionError, match="exactly one kernel"):
        resolve_session_with_assets(data)
