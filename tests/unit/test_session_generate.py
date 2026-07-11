"""Direct unit tests for catalog session generation."""

from __future__ import annotations

import pytest
from nodalarc.resolve_session import resolve_session
from nodalarc.session_generator import (
    assemble_session_document,
    constellation_source_mode,
    load_constellation_preset_response,
    load_constellation_presets,
)

LEO_RING = "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"


def _generated_session(**kwargs):
    raw, warnings = assemble_session_document(**kwargs)
    resolved = resolve_session(raw)
    return raw, resolved, warnings


def test_load_constellation_presets_scans_catalog_constellations() -> None:
    presets = load_constellation_presets()

    assert {
        "earth-leo-ring-36",
        "earth-leo-walker-delta-176",
        "earth-leo-polar-36",
        "earth-meo-gps-24",
        "earth-geo-ring-8",
        "earth-heo-molniya-3",
        "luna-polar-2",
    }.issubset(presets)
    assert all(p.constellation.startswith("nodalarc:constellations/") for p in presets.values())
    assert all(p.ground_stations.startswith("nodalarc:site-sets/") for p in presets.values())
    assert all(p.capability.source_kind == "constellation" for p in presets.values())


def test_constellation_preset_capabilities_follow_catalog_orbit_and_runtime_support() -> None:
    response = load_constellation_preset_response()
    presets = {preset.name: preset for preset in response.presets}

    earth = presets["earth-leo-ring-36"]
    assert earth.capability.runtime_supported_propagators == (
        "j2_mean_elements",
        "two_body",
    )
    assert earth.capability.default_propagator == "j2_mean_elements"
    assert earth.capability.unavailable_reason is None

    luna = presets["luna-polar-2"]
    assert luna.capability.runtime_supported_propagators == (
        "j2_mean_elements",
        "two_body",
    )
    assert luna.capability.default_propagator == "two_body"

    nrho = presets["luna-nrho-relay-1"]
    assert nrho.capability.runtime_supported_propagators == ()
    assert nrho.capability.default_propagator is None
    assert "crtbp" in str(nrho.capability.unavailable_reason)
    assert "Kepler elements cannot represent" in str(nrho.capability.unavailable_reason)

    assert response.custom_geometry.source_kind == "custom_geometry"
    assert response.custom_geometry.runtime_supported_propagators == (
        "j2_mean_elements",
        "two_body",
    )
    assert response.custom_geometry.default_propagator == "j2_mean_elements"
    assert response.custom_geometry_seed.pattern == "walker_delta"
    assert response.custom_geometry_seed.planes == 4
    assert str(response.custom_geometry_default_node).startswith("nodalarc:nodes/space/")
    assert tuple(model.id for model in response.orbit_models) == (
        "j2_mean_elements",
        "two_body",
        "sgp4_tle",
    )


def test_constellation_source_mode_reports_catalog_wrapper() -> None:
    assert (
        constellation_source_mode("nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml")
        == "constellation"
    )
    assert constellation_source_mode("/tmp/outside.yaml") is None


def test_generate_catalog_session_yaml_round_trips_through_resolver() -> None:
    raw, resolved, warnings = _generated_session(
        constellation=LEO_RING,
        protocol="isis",
        extensions=["te", "mpls"],
        orbit_propagator="j2_mean_elements",
        area_strategy="per_plane",
        ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    )

    assert warnings == []
    assert "constellation" not in raw
    assert "ground_stations" not in raw
    assert raw["segments"][0]["source"].startswith("nodalarc:constellations/")
    assert raw["segments"][1]["placement"]["from_site_set"].startswith("nodalarc:site-sets/")
    # orbit.default_propagator is inert grammar (orbit primitives own their
    # propagator) — generated sessions must not emit it; the generator instead
    # validates the requested propagator against the resolved orbits.
    assert "orbit" not in raw
    assert {n.orbit.propagator for n in resolved.nodes if n.orbit is not None} == {
        "j2_mean_elements"
    }
    assert raw["routing"]["domains"][0]["protocol"] == "isis"
    assert raw["routing"]["domains"][0]["capabilities"] == {
        "mpls": {},
        "traffic_engineering": {},
    }
    assert resolved.routing_domains[0].protocol == "isis"
    assert resolved.nodes


def test_generated_space_segment_is_named_by_orbit_regime() -> None:
    # Segment id drives runtime node identity ({segment}-{local}): a wizard
    # LEO session must produce leo-sat-* ids, matching the shipped sessions'
    # orbit-derived naming, with the rule/pool ids following the segment.
    cases = {
        "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml": "leo",
        "nodalarc:constellations/earth/meo/earth-meo-gps-24.yaml": "meo",
        "nodalarc:constellations/earth/geo/earth-geo-ring-8.yaml": "geo",
        "nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml": "heo",
    }
    for constellation, expected in cases.items():
        raw, resolved, _warnings = _generated_session(
            constellation=constellation,
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
        )
        assert raw["segments"][0]["id"] == expected, constellation
        assert raw["link_rules"][0]["id"] == f"{expected}_access"
        assert raw["link_rules"][1]["id"] == f"{expected}_isl"
        ground_endpoint, space_endpoint = raw["link_rules"][0]["endpoints"]
        expected_mount = "access_ka"
        assert {tuple(clause.items())[0] for clause in ground_endpoint["select"]["all"]} == {
            ("segment", "ground"),
            ("tag", expected),
        }
        assert {tuple(clause.items())[0] for clause in ground_endpoint["terminal"]["all"]} == {
            ("role", "access"),
            ("medium", "rf"),
            ("mount", expected_mount),
        }
        assert {tuple(clause.items())[0] for clause in space_endpoint["terminal"]["all"]} == {
            ("role", "access"),
            ("medium", "rf"),
            ("mount", expected_mount),
        }
        sat_ids = [n.node_id for n in resolved.nodes if n.orbit is not None]
        assert sat_ids and all(node_id.startswith(f"{expected}-") for node_id in sat_ids), (
            constellation,
            sat_ids[:3],
        )


def test_generate_catalog_session_uses_explicit_site_set_reference() -> None:
    presets = load_constellation_presets()
    site_set_ref = presets["earth-leo-ring-36"].ground_stations
    raw, _resolved, _warnings = _generated_session(
        constellation=LEO_RING,
        protocol="ospf",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_stations=site_set_ref,
    )

    assert raw["segments"][1]["placement"]["from_site_set"] == site_set_ref


def test_generate_session_requires_catalog_references() -> None:
    with pytest.raises(ValueError, match="must be a nodalarc:<path> or user:<path> reference"):
        assemble_session_document(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
        )

    with pytest.raises(ValueError, match="must be a nodalarc:<path> or user:<path> reference"):
        assemble_session_document(
            constellation=LEO_RING,
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            ground_stations=["denver", "hawthorne"],
        )


def test_longest_remaining_pass_generation_requires_horizon() -> None:
    with pytest.raises(ValueError, match="ground_selection_lookahead_horizon_ticks"):
        assemble_session_document(
            constellation=LEO_RING,
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            ground_policy="longest_remaining_pass",
        )


def test_longest_remaining_pass_generation_sets_policy() -> None:
    raw, resolved, _warnings = _generated_session(
        constellation=LEO_RING,
        protocol="isis",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_policy="longest_remaining_pass",
        ground_selection_lookahead_horizon_ticks=600,
    )

    scheduling = raw["segments"][1]["apply"]["scheduling"]
    assert scheduling["selection_policy"] == {
        "longest_remaining_pass": {"lookahead_horizon_ticks": 600}
    }
    assert all(
        node.ground_scheduling is None
        or node.ground_scheduling.selection_policy.longest_remaining_pass is not None
        for node in resolved.nodes
    )


def test_generate_catalog_session_rejects_sgp4_for_non_tle_source() -> None:
    with pytest.raises(ValueError, match="does not match the selected constellation"):
        assemble_session_document(
            constellation=LEO_RING,
            protocol="isis",
            extensions=[],
            orbit_propagator="sgp4_tle",
        )


def test_generated_session_carries_wizard_timers_into_resolved_domain() -> None:
    raw, resolved, warnings = _generated_session(
        constellation=LEO_RING,
        protocol="isis",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        timers={"hello_interval_s": 2, "hold_interval_s": 10, "bfd": {"enabled": True}},
    )

    assert warnings == []
    assert raw["routing"]["domains"][0]["timers"] == {
        "hello_interval_s": 2,
        "hold_interval_s": 10,
        "bfd": {"enabled": True},
    }
    domain = resolved.routing_domains[0]
    assert domain.timers.hello_interval_s == 2
    assert domain.timers.hold_interval_s == 10
    assert domain.timers.bfd.enabled is True
    # Untouched fields carry engine defaults on the resolved truth.
    assert domain.timers.spf.init_delay_ms == 50


def test_generated_session_with_default_timers_emits_no_timers_block() -> None:
    raw, resolved, _warnings = _generated_session(
        constellation=LEO_RING,
        protocol="isis",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        timers={"hello_interval_s": 1, "hold_interval_s": 3},
    )

    assert "timers" not in raw["routing"]["domains"][0]
    assert resolved.routing_domains[0].timers.hello_interval_s == 1


def test_generator_rejects_propagator_that_does_not_match_catalog_orbits() -> None:
    with pytest.raises(ValueError, match="does not match the selected"):
        assemble_session_document(
            constellation=LEO_RING,
            protocol="isis",
            extensions=[],
            orbit_propagator="two_body",
            ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        )
