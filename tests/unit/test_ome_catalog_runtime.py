# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME catalog-runtime seam tests."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
import yaml
from nodalarc.ome_inputs import ResolvedAddressingView, build_ome_inputs_from_resolved
from nodalarc.resolve_session import SessionResolutionError, load_session_resolution_from_file
from ome.main import _load_session_config, _read_runtime_run_id_file

from tests.conftest import build_segment_session_dict


def _write_session(tmp_path: Path, raw: dict, *, name: str = "session.yaml") -> Path:
    session_path = tmp_path / name
    session_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return session_path


def _resolved(tmp_path: Path, raw: dict | None = None):
    session_path = _write_session(
        tmp_path,
        raw
        or build_segment_session_dict(
            name="ome-catalog-runtime",
            constellation={"planes": {"count": 2, "sats_per_plane": 2}},
            ground_stations={"stations": ["a", "b"]},
        ),
    )
    return load_session_resolution_from_file(
        session_path,
        origin="test.ome",
        run_id="run-ome-0001",
    ).resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _luna_body() -> dict:
    return {
        "body": {
            "id": "luna",
            "display_name": "Luna",
            "gravitational_parameter_km3_s2": 4902.800066,
            "mean_radius_km": 1737.4,
            "equatorial_radius_km": 1738.1,
            "polar_radius_km": 1736.0,
            "reference": "test-fixture",
        }
    }


def _lunar_catalog_session(*, include_ephemeris: bool = True, include_sha: bool = True) -> dict:
    raw = build_segment_session_dict(
        name="ome-lunar-ephemeris",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    luna = _luna_body()
    orbit = raw["segments"][0]["source"]["constellation"]["orbit"]["orbit"]
    orbit["central_body"] = luna
    orbit["id"] = "luna-low-test"
    orbit["shape"] = {"altitude_km": 100}
    orbit["orientation"]["inclination_deg"] = 90
    for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
        site["site"]["frame"]["body_fixed"]["body"] = luna
        site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
    if include_ephemeris:
        kernel_path = Path("configs/ephemerides/de440s.bsp")
        kernel = {
            "id": "de440s",
            "path": str(kernel_path),
            "targets": [luna],
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
    session_path = _write_session(
        tmp_path,
        build_segment_session_dict(
            name="ome-catalog-load",
            constellation={"planes": {"count": 1, "sats_per_plane": 2}},
            ground_stations={"stations": ["a"]},
        ),
    )
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

    inactive_ground_node = "earth-us-co-denver-meo-gateway"
    active_ground_node = "earth-us-co-denver-leo-gateway"
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
    session_path = _write_session(tmp_path, _lunar_catalog_session(include_sha=False))

    with pytest.raises(SessionResolutionError, match="requires sha256"):
        load_session_resolution_from_file(session_path, origin="test.ome", run_id="run-ome-0001")


def test_resolver_rejects_non_earth_session_without_ephemeris_manifest(tmp_path: Path) -> None:
    raw = _lunar_catalog_session(include_ephemeris=False)
    session_path = _write_session(tmp_path, raw)

    with pytest.raises(SessionResolutionError, match="declares no ephemeris manifest"):
        load_session_resolution_from_file(session_path, origin="test.ome", run_id="run-ome-0001")


def test_ome_addressing_rejects_ambiguous_global_plane_slot_lookup(tmp_path: Path) -> None:
    raw = build_segment_session_dict(
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
                "allocation": "by_plane_slot",
            },
            {
                "id": "space-b-loopbacks-v6",
                "applies_to": {"segment": "space_b"},
                "ipv6_pool": "fd10::/64",
                "prefix_length": 128,
                "allocation": "by_plane_slot",
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


def test_ome_rejects_sgp4_until_tle_runtime_inputs_are_materialized(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path)
    nodes = [
        node.model_copy(update={"orbit": node.orbit.model_copy(update={"propagator": "sgp4_tle"})})
        if node.kind == "satellite" and node.orbit is not None
        else node
        for node in resolved.nodes
    ]
    sgp4 = resolved.model_copy(update={"nodes": tuple(nodes)})

    with pytest.raises(ValueError, match="TLE records"):
        build_ome_inputs_from_resolved(sgp4)
