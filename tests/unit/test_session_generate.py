"""Direct unit tests for catalog session generation."""

from __future__ import annotations

import pytest
import yaml
from nodalarc.resolve_session import resolve_session
from nodalarc.session_generator import (
    constellation_source_mode,
    generate_session_yaml,
    load_constellation_presets,
)


def _generated_session(**kwargs):
    yaml_text, warnings = generate_session_yaml(**kwargs)
    raw = yaml.safe_load(yaml_text)
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
    assert all(p.mode == "constellation" for p in presets.values())


def test_constellation_source_mode_reports_catalog_wrapper() -> None:
    assert (
        constellation_source_mode("nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml")
        == "constellation"
    )
    assert constellation_source_mode("/tmp/outside.yaml") is None


def test_generate_catalog_session_yaml_round_trips_through_resolver() -> None:
    raw, resolved, warnings = _generated_session(
        constellation="earth-leo-ring-36",
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
    assert raw["orbit"]["default_propagator"] == "j2_mean_elements"
    assert raw["routing"]["domains"][0]["protocol"] == "isis"
    assert raw["routing"]["domains"][0]["capabilities"] == {
        "mpls": {},
        "traffic_engineering": {},
    }
    assert resolved.routing_domains[0].protocol == "isis"
    assert resolved.nodes


def test_generate_catalog_session_supports_custom_site_set_object() -> None:
    presets = load_constellation_presets()
    site_set_ref = presets["earth-leo-ring-36"].ground_stations
    raw, _resolved, _warnings = _generated_session(
        constellation="earth-leo-ring-36",
        protocol="ospf",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_stations=site_set_ref,
    )

    assert raw["segments"][1]["placement"]["from_site_set"] == site_set_ref


def test_generate_catalog_session_rejects_retired_satellite_type_override() -> None:
    with pytest.raises(ValueError, match="satellite_type overrides are retired"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            satellite_type="starlink-v2",
        )


def test_generate_catalog_session_rejects_retired_ground_station_lists() -> None:
    with pytest.raises(ValueError, match="ground station name lists are retired"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            ground_stations=["denver", "hawthorne"],
        )


def test_longest_remaining_pass_generation_requires_horizon() -> None:
    with pytest.raises(ValueError, match="ground_selection_lookahead_horizon_ticks"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            ground_policy="longest_remaining_pass",
        )


def test_longest_remaining_pass_generation_sets_policy() -> None:
    raw, resolved, _warnings = _generated_session(
        constellation="earth-leo-ring-36",
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


def test_generate_catalog_session_rejects_future_sgp4_runtime_path() -> None:
    with pytest.raises(ValueError, match="structurally valid future grammar"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="sgp4_tle",
        )
