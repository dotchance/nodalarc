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


def test_generate_session_composes_chosen_satellite_primitive() -> None:
    """Sessions assemble from primitives: the constellation supplies
    geometry; the chosen space node primitive flies it. The composed
    constellation is inlined with the swapped node ref and a distinct id,
    and the result still resolves through the production resolver."""
    import yaml as _yaml
    from nodalarc.resolve_session import resolve_session

    text, _warnings = generate_session_yaml(
        constellation="earth-leo-ring-36",
        protocol="isis",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        satellite_type="leo-relay",
    )
    raw = _yaml.safe_load(text)
    space_source = raw["segments"][0]["source"]
    assert isinstance(space_source, dict)
    body = space_source["constellation"]
    assert body["node"] == "nodalarc:nodes/space/leo-relay.yaml"
    assert body["id"].endswith("-leo-relay")
    resolved = resolve_session(raw)
    sats = [n for n in resolved.nodes if n.kind == "satellite"]
    assert sats
    assert {t.terminal_id for t in sats[0].terminal_inventory} == {
        "access_ka",
        "isl_optical",
        "relay_optical",
    }


def test_generate_session_rejects_unknown_satellite_primitive() -> None:
    with pytest.raises(ValueError, match="Unknown satellite primitive"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            satellite_type="not-a-real-node",
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


def test_generated_session_carries_wizard_timers_into_resolved_domain() -> None:
    raw, resolved, warnings = _generated_session(
        constellation="earth-leo-ring-36",
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
        constellation="earth-leo-ring-36",
        protocol="isis",
        extensions=[],
        orbit_propagator="j2_mean_elements",
        ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        timers={"hello_interval_s": 1, "hold_interval_s": 3},
    )

    assert "timers" not in raw["routing"]["domains"][0]
    assert resolved.routing_domains[0].timers.hello_interval_s == 1


def test_generator_rejects_retired_routing_config_with_timers_pointer() -> None:
    with pytest.raises(ValueError, match="timers"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="j2_mean_elements",
            ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
            routing_config={"isis_hello_interval": 5},
        )


def test_generator_rejects_propagator_that_does_not_match_catalog_orbits() -> None:
    with pytest.raises(ValueError, match="does not match the selected"):
        generate_session_yaml(
            constellation="earth-leo-ring-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="two_body",
            ground_stations="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        )
