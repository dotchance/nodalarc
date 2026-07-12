# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME catalog-runtime seam tests."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
import yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.ome_inputs import ResolvedAddressingView, build_ome_inputs_from_resolved
from nodalarc.resolve_session import SessionResolutionError, load_session_resolution_from_file
from ome.event_stream import build_step_context
from ome.main import (
    _effective_ground_scheduling_for_runtime,
    _load_session_config,
    _read_runtime_run_id_file,
)

from tests.catalog_session_fixtures import (
    ISS_TLE_LINE_1,
    CatalogSessionFixture,
    build_catalog_session_fixture,
    install_tle_space_node_set,
    resolve_catalog_session,
)


def _resolved(tmp_path: Path, raw: dict | None = None):
    del tmp_path
    session = raw or build_catalog_session_fixture(
        name="ome-catalog-runtime",
        constellation={"planes": {"count": 2, "sats_per_plane": 2}},
        ground_stations={"stations": ["a", "b"]},
    )
    return resolve_catalog_session(
        session,
        source_context=SourceContext(origin="test.ome", run_id="run-ome-0001"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lunar_catalog_session(
    *, include_ephemeris: bool = True, include_sha: bool = True
) -> CatalogSessionFixture:
    raw = build_catalog_session_fixture(
        name="ome-lunar-ephemeris",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    assert raw.orbit_ref is not None
    orbit_document = raw.read_catalog(raw.orbit_ref)
    orbit = orbit_document["orbit"]
    orbit["central_body"] = "nodalarc:bodies/luna.yaml"
    orbit["shape"] = {"altitude_km": 100}
    orbit["orientation"]["inclination_deg"] = 90
    raw.write_catalog(raw.orbit_ref, orbit_document)
    for site_ref in raw.site_refs:
        site_document = raw.read_catalog(site_ref)
        site_document["site"]["frame"]["body_fixed"]["body"] = "nodalarc:bodies/luna.yaml"
        site_document["site"]["location"] = {
            "lat_deg": -80.0,
            "lon_deg": 0.0,
            "alt_m": 0.0,
        }
        raw.write_catalog(site_ref, site_document)
    if include_ephemeris:
        kernel_path = Path("configs/ephemerides/de440s.bsp")
        kernel = {
            "id": "de440s",
            "path": str(kernel_path),
            "targets": ["nodalarc:bodies/luna.yaml"],
            "frame": "gcrs",
            "coverage_start": "2026-06-01T00:00:00Z",
            "coverage_end": "2026-07-01T00:00:00Z",
        }
        if include_sha:
            kernel["sha256"] = _sha256(kernel_path)
        raw["ephemeris"] = {
            "provider": "skyfield_bsp",
            "quality_tier": "de440s",
            "kernels": [kernel],
        }
    return raw


def test_ome_loads_resolved_session_with_operator_runtime_identity(tmp_path: Path) -> None:
    session_path = Path("catalog/nodalarc/sessions/earth-leo-simple.yaml")
    run_id_file = tmp_path / "session_run_id"
    run_id_file.write_text("run-ome-0001\n", encoding="utf-8")

    cfg = _load_session_config(
        session_path,
        run_id=_read_runtime_run_id_file(run_id_file),
    )

    assert cfg.session_id == "run-ome-0001"
    assert cfg.resolved.source_context.run_id == "run-ome-0001"
    assert cfg.satellites
    assert cfg.gs_file is not None


def test_ome_run_id_sidecar_fails_loudly_when_missing_or_empty(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        _read_runtime_run_id_file(tmp_path / "missing")

    empty = tmp_path / "session_run_id"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        _read_runtime_run_id_file(empty)


def test_ome_inputs_are_resolved_owned_and_materialize_ground_candidates(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path)

    runtime = build_ome_inputs_from_resolved(resolved)

    resolved_satellite_ids = {node.node_id for node in resolved.nodes if node.kind == "satellite"}
    resolved_ground_ids = {node.node_id for node in resolved.nodes if node.kind == "ground_station"}
    assert {sat.node_id for sat in runtime.satellites} == resolved_satellite_ids
    assert runtime.gs_file is not None
    assert {station.name for station in runtime.gs_file.stations} == resolved_ground_ids
    assert runtime.ground_candidate_satellites_by_gs
    assert all(runtime.rule_map[pair].link_rule_id for pair in runtime.rule_map)
    assert runtime.ground_link_model == "terminal_physics"
    assert all(
        isinstance(terminal.boresight, SatGroundTerminalBoresight)
        and terminal.field_of_regard_deg is not None
        and terminal.max_tracking_rate_deg_s is not None
        and terminal.max_range_km is not None
        for satellite in runtime.satellites
        for terminal in satellite.ground_terminals
    )
    assert all(
        isinstance(terminal.boresight, TerminalBoresight)
        and terminal.field_of_regard_deg is not None
        and terminal.max_tracking_rate_deg_s is not None
        and terminal.max_range_km is not None
        for station in runtime.gs_file.stations
        for terminal in station.terminals or ()
    )


def _step_context_from_bundle(cfg):
    return build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )


def test_tdrs_access_rule_selects_reciprocal_ka_mounts_with_global_indices() -> None:
    cfg = _load_session_config(
        Path("catalog/nodalarc/sessions/earth-geo-tdrs.yaml"),
        run_id="run-ome-tdrs-0001",
    )

    access_rule = next(rule for rule in cfg.resolved.link_rules if rule.rule_id == "geo_access")
    assert tuple(endpoint.terminal_id for endpoint in access_rule.endpoints) == (
        "tdrs_ka_sa",
        "ka_sa",
    )
    assert cfg.ground_link_model == "terminal_physics"
    assert {
        candidate.bandwidth_mbps
        for candidate in cfg.resolved.link_candidates
        if candidate.kind == "access"
    } == {50.0}
    assert cfg.gs_file is not None
    assert all(
        tuple(
            (terminal.id, terminal.interface_indices, terminal.bandwidth_mbps)
            for terminal in station.terminals or ()
        )
        == (("tdrs_ka_sa", (2, 3), 50.0),)
        for station in cfg.gs_file.stations
    )
    assert all(
        tuple(
            (terminal.interface_indices, terminal.bandwidth_mbps)
            for terminal in satellite.ground_terminals
        )
        == (((5, 6), 50.0),)
        for satellite in cfg.satellites
    )

    context = _step_context_from_bundle(cfg)

    assert set(context.gs_terminal_indices.values()) == {(2, 3)}
    assert set(context.gs_terminal_counts.values()) == {2}
    assert {
        tuple(pool["earth"]) for pool in context.sat_ground_terminal_indices_by_body.values()
    } == {(5, 6)}


def test_inmarsat_access_rule_remains_on_standard_geo_mounts() -> None:
    cfg = _load_session_config(
        Path("catalog/nodalarc/sessions/earth-geo-inmarsat.yaml"),
        run_id="run-ome-inmarsat-0001",
    )

    access_rule = next(rule for rule in cfg.resolved.link_rules if rule.rule_id == "geo_access")
    assert tuple(endpoint.terminal_id for endpoint in access_rule.endpoints) == (
        "access_ka",
        "access_ka",
    )
    assert {
        candidate.bandwidth_mbps
        for candidate in cfg.resolved.link_candidates
        if candidate.kind == "access"
    } == {750.0}
    assert cfg.gs_file is not None
    assert all(
        tuple(terminal.id for terminal in station.terminals or ()) == ("access_ka",)
        and tuple(terminal.interface_indices for terminal in station.terminals or ()) == ((0, 1),)
        for station in cfg.gs_file.stations
    )
    assert all(
        tuple(terminal.interface_indices for terminal in satellite.ground_terminals) == ((0,),)
        for satellite in cfg.satellites
    )


def test_generic_selector_rejects_heterogeneous_geo_ground_mounts(
    tmp_path: Path,
) -> None:
    source = Path("catalog/nodalarc/sessions/earth-geo-tdrs.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    predicates = raw["link_rules"][0]["endpoints"][0]["terminal"]["all"]
    raw["link_rules"][0]["endpoints"][0]["terminal"]["all"] = [
        predicate for predicate in predicates if "mount" not in predicate
    ]
    session_path = tmp_path / "heterogeneous-geo-access.yaml"
    session_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(SessionResolutionError, match="matches multiple terminal mounts"):
        load_session_resolution_from_file(session_path)


def test_ome_maps_resolved_two_body_to_truthful_runtime_propagator_id(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path)
    nodes = [
        node.model_copy(update={"orbit": node.orbit.model_copy(update={"propagator": "two_body"})})
        if node.kind == "satellite" and node.orbit is not None
        else node
        for node in resolved.nodes
    ]
    resolved = resolved.model_copy(update={"nodes": tuple(nodes)})

    runtime = build_ome_inputs_from_resolved(resolved)

    assert runtime.propagator_id == "two-body"


def test_ome_inputs_support_mixed_resolved_satellite_propagators(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path)
    nodes = []
    changed = False
    for node in resolved.nodes:
        if node.kind == "satellite" and node.orbit is not None and not changed:
            nodes.append(
                node.model_copy(
                    update={"orbit": node.orbit.model_copy(update={"propagator": "two_body"})}
                )
            )
            changed = True
        else:
            nodes.append(node)
    mixed = resolved.model_copy(update={"nodes": tuple(nodes)})

    runtime = build_ome_inputs_from_resolved(mixed)

    assert runtime.propagator_id == "mixed"
    assert {sat.propagator_id for sat in runtime.satellites} == {
        "two-body",
        "j2-mean-elements",
    }


def test_ome_inputs_ignore_ground_nodes_without_declared_access_candidates() -> None:
    resolved = load_session_resolution_from_file(
        Path("catalog/nodalarc/sessions/earth-leo-simple.yaml"),
        origin="test.ome",
        run_id="run-ome-0001",
    ).resolved

    inactive_ground_node = "earth-us-co-denver-gw2"
    active_ground_node = "earth-us-co-denver-gw1"
    resolved_node_ids = {node.node_id for node in resolved.nodes}
    candidates = resolved.ground_candidate_satellites_by_gs()

    assert inactive_ground_node in resolved_node_ids
    assert active_ground_node in candidates
    assert inactive_ground_node not in candidates

    runtime = build_ome_inputs_from_resolved(resolved)

    assert runtime.gs_file is not None
    assert {station.name for station in runtime.gs_file.stations} == set(candidates)
    assert inactive_ground_node not in {station.name for station in runtime.gs_file.stations}
    assert inactive_ground_node in runtime.node_metadata


def test_ome_materializes_non_earth_ephemeris_provider_from_resolved_manifest(
    tmp_path: Path,
) -> None:
    resolved = _resolved(tmp_path, _lunar_catalog_session())

    runtime = build_ome_inputs_from_resolved(resolved)

    assert runtime.active_bodies == frozenset({"luna"})
    assert runtime.body_ephemeris is not None
    state = runtime.body_ephemeris.body_state("luna", 1780876800.0)
    assert state.body_id == "luna"
    assert (
        math.sqrt(state.position_km.x**2 + state.position_km.y**2 + state.position_km.z**2)
        > 300_000
    )


def test_resolver_rejects_non_earth_ephemeris_manifest_without_checksum(tmp_path: Path) -> None:
    # Manifest runtime validation is a resolve-time gate: a sha-less manifest
    # must fail at upload/deploy, never reach OME input construction.
    del tmp_path
    raw = _lunar_catalog_session(include_sha=False)

    with pytest.raises(SessionResolutionError, match="requires sha256"):
        resolve_catalog_session(raw)


def test_resolver_rejects_non_earth_session_without_ephemeris_manifest(tmp_path: Path) -> None:
    del tmp_path
    raw = _lunar_catalog_session(include_ephemeris=False)

    with pytest.raises(SessionResolutionError, match="declares no ephemeris manifest"):
        resolve_catalog_session(raw)


def test_ome_addressing_rejects_ambiguous_global_plane_slot_lookup(tmp_path: Path) -> None:
    raw = build_catalog_session_fixture(
        name="ome-plane-slot-ambiguity",
        constellation={"planes": {"count": 1, "sats_per_plane": 1}},
        ground_stations={"stations": ["a"]},
    )
    raw["segments"].insert(1, {"id": "space_b", "source": raw["segments"][0]["source"]})
    raw["routing"]["domains"][0]["selectors"][0]["any"].append({"segment": "space_b"})
    raw["addressing"]["loopbacks"].extend(
        [
            {
                "id": "space-b-loopbacks-v4",
                "applies_to": {"segment": "space_b"},
                "ipv4_pool": "10.10.0.0/16",
                "prefix_length": 32,
                "allocation": "by_node_order",
            },
            {
                "id": "space-b-loopbacks-v6",
                "applies_to": {"segment": "space_b"},
                "ipv6_pool": "fd10::/64",
                "prefix_length": 128,
                "allocation": "by_node_order",
            },
        ]
    )
    resolved = _resolved(tmp_path, raw)

    view = ResolvedAddressingView(resolved)

    with pytest.raises(KeyError, match="not globally unique"):
        view.sat_id(0, 0)


def test_ome_materializes_eccentric_orbits_into_runtime_elements(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path)
    nodes = list(resolved.nodes)
    for index, node in enumerate(nodes):
        if node.kind == "satellite":
            assert node.orbit is not None
            nodes[index] = node.model_copy(
                update={
                    "orbit": node.orbit.model_copy(
                        update={
                            "orbit_id": "test-eccentric",
                            "eccentricity": 0.5,
                            "argument_of_perigee_deg": 270.0,
                            "mean_anomaly_deg": 12.0,
                        }
                    )
                }
            )
            break
    eccentric = resolved.model_copy(update={"nodes": tuple(nodes)})

    runtime = build_ome_inputs_from_resolved(eccentric)
    sat = runtime.satellites[0]

    assert sat.elements.eccentricity == 0.5
    assert math.degrees(sat.elements.argument_of_perigee_rad) == pytest.approx(270.0)
    assert math.degrees(sat.elements.mean_anomaly_rad) == pytest.approx(12.0)


def test_ome_materializes_canonical_sgp4_tle_runtime_inputs(tmp_path: Path) -> None:
    raw = build_catalog_session_fixture(
        name="ome-sgp4-runtime",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    install_tle_space_node_set(raw)
    resolved = _resolved(tmp_path, raw)

    runtime = build_ome_inputs_from_resolved(resolved)
    iss = next(satellite for satellite in runtime.satellites if satellite.local_node_id == "iss")

    assert runtime.propagator_id == "sgp4-tle"
    assert iss.tle_line_1 == ISS_TLE_LINE_1
    assert iss.tle_line_2 is not None
    assert iss.norad_id == 25544
    assert iss.propagator_id == "sgp4-tle"


def test_rule_endpoint_elevation_mask_is_enforced_not_just_displayed(tmp_path: Path) -> None:
    """One mask derivation for enforcement and display: a rule-endpoint
    min_elevation_deg stricter than the terminal mask must reach OME's
    ground-station inputs, not only the UI map."""
    raw = build_catalog_session_fixture(
        name="ome-endpoint-mask",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    raw["link_rules"][0]["endpoints"][0]["min_elevation_deg"] = 37
    resolved = _resolved(tmp_path, raw)

    masks = resolved.effective_ground_min_elevation_by_gs()
    gs_id = next(node.node_id for node in resolved.nodes if node.kind == "ground_station")
    assert masks[gs_id] >= 37.0

    runtime = build_ome_inputs_from_resolved(resolved)
    assert runtime.gs_file is not None
    station = next(s for s in runtime.gs_file.stations if s.name == gs_id)
    assert station.min_elevation_deg == masks[gs_id]
