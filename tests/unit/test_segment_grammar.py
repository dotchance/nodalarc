# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structural-schema tests for the segment session grammar (M1 Phase A).

These cover the structural layer only: shape, discriminated-union dispatch,
extra-key rejection, field-level validators, future-grammar structural validity,
and JSON Schema export. Cross-object/identity-mode semantics are owned by the
resolver and tested separately.
"""

import math

import pytest
from nodalarc.models.ephemeris import EphemerisConfig
from nodalarc.models.identity import IdentityConfig, IdentityMode
from nodalarc.models.link_rules import (
    ExplicitPairsTopology,
    LinkRule,
    LinkRuleConstraints,
    NearestNTopology,
    NodeSelector,
    VisibleCandidatesTopology,
)
from nodalarc.models.segment_session import SegmentSessionConfig
from nodalarc.models.segments import (
    ConstellationSegment,
    GroundSchedulingPolicy,
    GroundSegment,
    LagrangePointSegment,
    SegmentClock,
    SpaceNodeSegment,
    SpaceNodeSetSegment,
    StateVector,
)
from pydantic import ValidationError


def _minimal_session(**overrides) -> dict:
    base = {
        "session": {"name": "demo"},
        "segments": [
            {
                "id": "leo",
                "kind": "constellation",
                "source": "configs/constellations/demo-36.yaml",
                "namespace": "leo",
                "central_body": "earth",
            },
            {
                "id": "gnd",
                "kind": "ground_set",
                "source": "configs/ground-stations/sets/demo.yaml",
                "reference_body": "earth",
                "namespace": "gnd",
            },
        ],
        "link_rules": [
            {
                "id": "access",
                "kind": "access",
                "endpoints": [
                    {"selector": {"segment": "gnd"}, "terminal_role": "ground"},
                    {"selector": {"segment": "leo"}, "terminal_role": "ground"},
                ],
                "topology": {"mode": "visible_candidates"},
            }
        ],
        "orbit": {"propagator": "j2-mean-elements"},
        "routing": {"protocol": "isis"},
        "simulation": {"candidate_limits": {"max_pairs_per_rule": 5000}},
    }
    base.update(overrides)
    return base


# --- Top-level session shape ---


def test_valid_segment_session_parses():
    s = SegmentSessionConfig.model_validate(_minimal_session())
    assert len(s.segments) == 2
    assert isinstance(s.segments[0], ConstellationSegment)
    assert isinstance(s.segments[1], GroundSegment)
    assert isinstance(s.link_rules[0], LinkRule)


def test_segments_required_non_empty():
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(_minimal_session(segments=[]))


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(_minimal_session(satellite_type="starlink"))


def test_orbit_and_routing_required():
    body = _minimal_session()
    del body["orbit"]
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)


# --- Identity modes ---


def test_identity_defaults_to_segment_namespaced():
    s = SegmentSessionConfig.model_validate(_minimal_session())
    assert s.identity.mode is IdentityMode.SEGMENT_NAMESPACED


def test_legacy_identity_modes_rejected():
    for mode in ("legacy_compatible", "legacy_identity"):
        with pytest.raises(ValidationError, match="segment_namespaced"):
            IdentityConfig.model_validate({"mode": mode})


def test_identity_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        IdentityConfig.model_validate({"mode": "namespaced"})


# --- Segment discriminated union (incl. runtime-future kinds parse structurally) ---


def test_space_node_segment_parses():
    seg = SpaceNodeSegment.model_validate(
        {
            "id": "relay",
            "kind": "space_node",
            "namespace": "relay",
            "satellite_type": "cislunar-relay",
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
    assert seg.node.id == "relay"


def test_future_lagrange_point_parses_structurally():
    seg = LagrangePointSegment.model_validate(
        {
            "id": "eml1",
            "kind": "lagrange_point",
            "namespace": "em-l1",
            "satellite_type": "cislunar-relay",
            "frame": {
                "primary_body": "earth",
                "secondary_body": "luna",
                "point": "L1",
                "ephemeris": {"model": "lagrange_approximation"},
            },
        }
    )
    assert seg.frame.point == "L1"


def test_future_space_node_set_parses_structurally():
    seg = SpaceNodeSetSegment.model_validate(
        {
            "id": "relays",
            "kind": "space_node_set",
            "namespace": "relays",
            "satellite_type": "cislunar-relay",
            "nodes": [
                {
                    "id": "relay-a",
                    "state": {
                        "frame": "gcrs",
                        "position_km": [400000.0, 0.0, 0.0],
                        "velocity_km_s": [0.0, 1.0, 0.0],
                    },
                }
            ],
        }
    )
    assert len(seg.nodes) == 1


def test_future_sun_frame_body_parses_structurally():
    # "sun" is a frame body (runtime-future); it must parse at the structural layer.
    seg = LagrangePointSegment.model_validate(
        {
            "id": "sml1",
            "kind": "lagrange_point",
            "namespace": "sm-l1",
            "satellite_type": "deep-space-relay",
            "frame": {
                "primary_body": "sun",
                "secondary_body": "mars",
                "point": "L1",
                "ephemeris": {"model": "external_ephemeris", "source": "x.spice"},
            },
        }
    )
    assert seg.frame.primary_body == "sun"


@pytest.mark.parametrize(
    "ephemeris",
    [
        {"model": "external_ephemeris"},
        {"model": "external_ephemeris", "source": ""},
        {"model": "configured_state"},
        {
            "model": "configured_state",
            "state": {"frame": "gcrs", "position_km": [1, 2, 3], "velocity_km_s": [0, 0, 0]},
            "source": "x.spice",
        },
        {"model": "lagrange_approximation", "source": "x.spice"},
    ],
)
def test_lagrange_ephemeris_rejects_mode_incoherent_fields(ephemeris):
    with pytest.raises(ValidationError):
        LagrangePointSegment.model_validate(
            {
                "id": "eml1",
                "kind": "lagrange_point",
                "namespace": "em-l1",
                "satellite_type": "cislunar-relay",
                "frame": {
                    "primary_body": "earth",
                    "secondary_body": "luna",
                    "point": "L1",
                    "ephemeris": ephemeris,
                },
            }
        )


def test_configured_lagrange_ephemeris_requires_state_and_parses():
    seg = LagrangePointSegment.model_validate(
        {
            "id": "eml1",
            "kind": "lagrange_point",
            "namespace": "em-l1",
            "satellite_type": "cislunar-relay",
            "frame": {
                "primary_body": "earth",
                "secondary_body": "luna",
                "point": "L1",
                "ephemeris": {
                    "model": "configured_state",
                    "state": {
                        "frame": "gcrs",
                        "position_km": [326400.0, 0.0, 0.0],
                        "velocity_km_s": [0.0, 1.0, 0.0],
                    },
                },
            },
        }
    )
    assert seg.frame.ephemeris.model == "configured_state"


def test_constellation_inline_source_is_structurally_validated():
    # A path string source is accepted verbatim when it is a real reference.
    seg = ConstellationSegment.model_validate(
        {
            "id": "leo",
            "kind": "constellation",
            "source": "configs/constellations/demo-36.yaml",
            "namespace": "leo",
            "central_body": "earth",
        }
    )
    assert seg.source == "configs/constellations/demo-36.yaml"
    # An inline dict source is validated against the ConstellationConfig union, so
    # a malformed inline fails structurally rather than being silently accepted.
    with pytest.raises(ValidationError):
        ConstellationSegment.model_validate(
            {
                "id": "leo",
                "kind": "constellation",
                "source": {"mode": "not-a-real-mode"},
                "namespace": "leo",
                "central_body": "earth",
            }
        )


@pytest.mark.parametrize(
    "segment_factory",
    [
        lambda source: ConstellationSegment.model_validate(
            {
                "id": "leo",
                "kind": "constellation",
                "source": source,
                "namespace": "leo",
                "central_body": "earth",
            }
        ),
        lambda source: GroundSegment.model_validate(
            {"id": "gnd", "kind": "ground_set", "source": source, "reference_body": "earth"}
        ),
    ],
)
def test_segment_source_path_rejects_empty_or_whitespace(segment_factory):
    for source in ("", " "):
        with pytest.raises(ValidationError):
            segment_factory(source)


def test_segment_identifier_pattern_enforced():
    # Uppercase / dotted segment ids violate the Identifier pattern.
    with pytest.raises(ValidationError):
        ConstellationSegment.model_validate(
            {
                "id": "Earth.LEO",
                "kind": "constellation",
                "source": "x.yaml",
                "namespace": "leo",
                "central_body": "earth",
            }
        )


# --- Segment clock ---


def test_segment_clock_affine_requires_positive_rate():
    # rate=0 is rejected by the positive-finite field constraint.
    with pytest.raises(ValidationError, match="greater than 0"):
        SegmentClock.model_validate({"model": "affine", "rate": 0})
    # rate omitted is rejected by the affine cross-field rule.
    with pytest.raises(ValidationError, match="positive rate"):
        SegmentClock.model_validate({"model": "affine"})


def test_segment_clock_session_forbids_offset_and_rate():
    with pytest.raises(ValidationError, match="must not set offset_s or rate"):
        SegmentClock.model_validate({"model": "session", "offset_s": 5.0})


@pytest.mark.parametrize(
    "override",
    [
        {"ranking_order": []},
        {"ranking_order": ["selection_score"]},
        {"ranking_order": ["lex_pair"]},
        {"ranking_order": ["selection_score", "selection_score", "lex_pair"]},
        {"mbb_overlap_ticks": -1},
        {"mbb_reserve": -1},
        {"mbb_reserve": 2},
        {"bbm_acquire_timeout_ticks": 2},
        {"handover_mode": "mbb", "mbb_overlap_ticks": 0},
        {"handover_mode": "mbb", "mbb_reserve": 0},
    ],
)
def test_ground_scheduling_policy_rejects_impossible_supplied_values(override):
    with pytest.raises(ValidationError):
        GroundSchedulingPolicy.model_validate(override)


def test_ground_scheduling_policy_accepts_valid_partial_values():
    policy = GroundSchedulingPolicy.model_validate(
        {
            "ranking_order": ["selection_score", "lex_pair"],
            "handover_mode": "mbb",
            "mbb_overlap_ticks": 1,
            "mbb_reserve": 1,
            "bbm_acquire_timeout_ticks": 1,
        }
    )
    assert policy.ranking_order == ("selection_score", "lex_pair")


# --- Link rules / topology / endpoints ---


def test_topology_discriminator_dispatch():
    assert isinstance(
        VisibleCandidatesTopology.model_validate({"mode": "visible_candidates"}),
        VisibleCandidatesTopology,
    )
    nn = NearestNTopology.model_validate({"mode": "nearest_n", "n": 2})
    assert nn.n == 2


def test_nearest_n_requires_positive_n():
    with pytest.raises(ValidationError):
        NearestNTopology.model_validate({"mode": "nearest_n", "n": 0})


@pytest.mark.parametrize(
    "constraints",
    [
        {"max_links_per_node": 0},
        {"max_links_per_node": -1},
        {"max_links_per_node": {"leo": 0}},
        {"max_links_per_node": {"leo": -2}},
        {"max_range_km": 0},
        {"max_range_km": -5},
        {"max_range_km": math.inf},
        {"max_range_km": math.nan},
    ],
)
def test_link_rule_constraints_reject_impossible_numbers(constraints):
    with pytest.raises(ValidationError):
        LinkRuleConstraints.model_validate(constraints)


def test_link_rule_constraints_accept_valid_numbers():
    c = LinkRuleConstraints.model_validate(
        {"max_links_per_node": {"leo": 4}, "max_range_km": 5000.0}
    )
    assert c.max_range_km == 5000.0
    assert c.max_links_per_node["leo"] == 4


@pytest.mark.parametrize("bad", [{"planes": [-1]}, {"slots": [-2]}])
def test_node_selector_rejects_negative_indices(bad):
    with pytest.raises(ValidationError):
        NodeSelector.model_validate({"segment": "leo", **bad})


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_state_vector_rejects_non_finite_geometry(bad):
    with pytest.raises(ValidationError):
        StateVector.model_validate(
            {"frame": "gcrs", "position_km": (bad, 0.0, 0.0), "velocity_km_s": (0.0, 0.0, 0.0)}
        )


def test_segment_clock_rejects_non_finite_rate():
    with pytest.raises(ValidationError):
        SegmentClock.model_validate({"model": "affine", "rate": math.inf})


def test_explicit_pairs_requires_pairs():
    with pytest.raises(ValidationError):
        ExplicitPairsTopology.model_validate({"mode": "explicit_pairs", "pairs": []})


def test_link_rule_requires_exactly_two_endpoints():
    one_endpoint = {
        "id": "bad",
        "kind": "access",
        "endpoints": [{"selector": {"segment": "gnd"}, "terminal_role": "ground"}],
        "topology": {"mode": "visible_candidates"},
    }
    with pytest.raises(ValidationError):
        LinkRule.model_validate(one_endpoint)


def test_link_rule_enabled_defaults_true():
    rule = LinkRule.model_validate(
        {
            "id": "r",
            "kind": "relay",
            "endpoints": [
                {"selector": {"segment": "a"}, "terminal_role": "relay"},
                {"selector": {"segment": "b"}, "terminal_role": "relay"},
            ],
            "topology": {"mode": "visible_candidates"},
        }
    )
    assert rule.enabled is True


def test_protocol_boundary_future_adapter_parses_structurally():
    rule = LinkRule.model_validate(
        {
            "id": "r",
            "kind": "inter_body_relay",
            "endpoints": [
                {"selector": {"segment": "a"}, "terminal_role": "relay"},
                {"selector": {"segment": "b"}, "terminal_role": "relay"},
            ],
            "topology": {"mode": "visible_candidates"},
            "protocol_boundary": {"enabled": True, "adapter": "dtn_bundle"},
        }
    )
    assert rule.protocol_boundary.adapter == "dtn_bundle"


# --- Candidate limits ---


def test_candidate_limits_positive():
    with pytest.raises(ValidationError, match="max_pairs_per_rule must be > 0"):
        SegmentSessionConfig.model_validate(
            _minimal_session(simulation={"candidate_limits": {"max_pairs_per_rule": 0}})
        )


# --- Ephemeris manifest ---


def test_ephemeris_manifest_parses():
    eph = EphemerisConfig.model_validate(
        {
            "provider": "skyfield_bsp",
            "quality_tier": "jpl_de_bsp",
            "kernels": [
                {
                    "id": "de440",
                    "path": "kernels/de440.bsp",
                    "checksum": "abc123",
                    "targets": ["earth", "luna"],
                    "frame": "gcrs",
                    "coverage_start": "2025-01-01T00:00:00Z",
                    "coverage_end": "2030-01-01T00:00:00Z",
                }
            ],
        }
    )
    assert eph.kernels[0].targets == ["earth", "luna"]


def test_ephemeris_rejects_naive_datetimes():
    with pytest.raises(ValidationError):
        EphemerisConfig.model_validate(
            {
                "provider": "skyfield_bsp",
                "quality_tier": "jpl_de_bsp",
                "kernels": [
                    {
                        "id": "de440",
                        "path": "kernels/de440.bsp",
                        "checksum": "abc123",
                        "targets": ["earth", "luna"],
                        "frame": "gcrs",
                        "coverage_start": "2025-01-01T00:00:00",  # naive: rejected
                        "coverage_end": "2030-01-01T00:00:00",
                    }
                ],
            }
        )


def test_ephemeris_rejects_reversed_window():
    with pytest.raises(ValidationError, match="coverage_end must be after"):
        EphemerisConfig.model_validate(
            {
                "provider": "skyfield_bsp",
                "quality_tier": "jpl_de_bsp",
                "kernels": [
                    {
                        "id": "de440",
                        "path": "kernels/de440.bsp",
                        "checksum": "abc123",
                        "targets": ["earth"],
                        "frame": "gcrs",
                        "coverage_start": "2030-01-01T00:00:00Z",
                        "coverage_end": "2025-01-01T00:00:00Z",
                    }
                ],
            }
        )


def test_ephemeris_requires_at_least_one_kernel():
    with pytest.raises(ValidationError):
        EphemerisConfig.model_validate(
            {"provider": "skyfield_bsp", "quality_tier": "jpl_de_bsp", "kernels": []}
        )


# --- JSON Schema export ---


def test_json_schema_generation():
    schema = SegmentSessionConfig.model_json_schema()
    assert "segments" in schema["properties"]
    assert "link_rules" in schema["properties"]
    assert "identity" in schema["properties"]
