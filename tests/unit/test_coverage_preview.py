"""Coverage preview end-to-end test.

Calls compute_coverage_preview with real constellation and ground station
configs. Verifies the full pipeline: constellation loading, ground station
loading, orbital propagation, visibility computation, ISL/GS statistics,
and return type structure.

This test exists because precompute_timeline_window changed its return
signature (3 values → 5 values for MBB checkpoint state) and the coverage
preview caller was not updated. No test exercised the coverage preview
path, so the break shipped silently. A user clicking "Preview Coverage"
in the wizard got a Python traceback instead of results.
"""

from __future__ import annotations

import pytest
from nodalarc.models.addressing import topology_summary
from nodalarc.models.coverage import CoveragePreviewResult
from nodalarc.ome_inputs import build_ome_inputs_from_resolved
from nodalarc.resolve_session import resolve_session_with_assets
from nodalarc.session_generator import generated_isl_topology
from ome.coverage_preview import _preview_segment_session, compute_coverage_preview


@pytest.fixture(scope="module")
def demo_preview() -> CoveragePreviewResult:
    """Run coverage preview once with the smallest real config."""
    return compute_coverage_preview(
        constellation_source="nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
        satellite_type_override=None,
        ground_stations_source="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    )


@pytest.fixture(scope="module")
def heo_preview() -> CoveragePreviewResult:
    """Run coverage preview on the shipped eccentric HEO catalog primitives."""
    return compute_coverage_preview(
        constellation_source="nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml",
        satellite_type_override=None,
        ground_stations_source="nodalarc:site-sets/earth/heo/earth-heo-gateway-sites.yaml",
    )


def test_returns_coverage_preview_result(demo_preview):
    assert isinstance(demo_preview, CoveragePreviewResult)


def test_orbital_period_physically_plausible(demo_preview):
    # LEO orbital periods range from ~87 min (160 km) to ~127 min (2000 km)
    # demo-36 is at 550 km → ~96 minutes → ~5760 seconds
    assert 5000 < demo_preview.orbital_period_s < 8000


def test_isl_statistics_consistent(demo_preview):
    isl = demo_preview.isl
    assert isl.total_possible > 0
    assert isl.formed_at_least_once >= 0
    assert isl.never_formed >= 0
    assert isl.formed_at_least_once + isl.never_formed == isl.total_possible
    assert 0 <= isl.feasibility_pct <= 100.0
    assert isl.min_active >= 0
    assert isl.max_active >= isl.min_active


def test_gs_statistics_consistent(demo_preview):
    gs = demo_preview.ground_stations
    assert gs.simultaneous_min >= 0
    assert gs.simultaneous_max >= gs.simultaneous_min
    assert gs.simultaneous_mean >= gs.simultaneous_min
    assert gs.simultaneous_mean <= gs.simultaneous_max
    assert gs.max_gap_s >= 0


def test_per_station_coverage_present(demo_preview):
    gs = demo_preview.ground_stations
    assert len(gs.per_station) > 0
    for name, station in gs.per_station.items():
        assert isinstance(name, str)
        assert 0 <= station.coverage_pct <= 100.0
        assert station.longest_gap_s >= 0
        assert isinstance(station.reason, str)
        assert len(station.reason) > 0


def test_demo_36_has_active_isls(demo_preview):
    # 36 sats in one plane with 4 ISL terminals → intra-plane ISLs should form
    assert demo_preview.isl.max_active > 0


def test_demo_36_has_gs_coverage(demo_preview):
    # demo GS set has stations within the 53-deg inclination coverage band
    gs = demo_preview.ground_stations
    has_coverage = any(s.coverage_pct > 0 for s in gs.per_station.values())
    assert has_coverage, "At least one ground station should have coverage"


def test_heo_preview_uses_eccentric_sampled_explanations(heo_preview):
    assert 40_000 < heo_preview.orbital_period_s < 45_000
    assert heo_preview.ground_stations.per_station

    reasons = [station.reason or "" for station in heo_preview.ground_stations.per_station.values()]
    assert all("Sampled eccentric orbit propagation" in reason for reason in reasons)
    assert all("inclination band" not in reason for reason in reasons)
    assert all("footprint edge" not in reason for reason in reasons)


def test_warnings_is_list(demo_preview):
    assert isinstance(demo_preview.warnings, list)


# --- Error cases ---


def test_missing_constellation_raises():
    with pytest.raises(ValueError, match="constellation is required"):
        compute_coverage_preview(
            None,
            None,
            "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        )


def test_missing_ground_stations_raises():
    with pytest.raises(ValueError, match="ground_stations is required"):
        compute_coverage_preview(
            "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
            None,
            None,
        )


def test_nonexistent_constellation_raises():
    with pytest.raises(FileNotFoundError):
        compute_coverage_preview(
            "nodalarc:constellations/earth/leo/nonexistent.yaml",
            None,
            "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        )


def test_nonexistent_ground_stations_raises():
    with pytest.raises(FileNotFoundError):
        compute_coverage_preview(
            "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
            None,
            "nodalarc:site-sets/earth/leo/nonexistent.yaml",
        )


def test_preview_composes_chosen_satellite_primitive():
    """Preview assembles from primitives exactly like generation: the chosen
    space node flies the constellation's geometry through the same resolver
    path; an unknown primitive is a typed rejection, never a fallback."""
    with pytest.raises(ValueError, match="Unknown satellite primitive"):
        compute_coverage_preview(
            constellation_source="nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
            satellite_type_override="generic-4isl",
            ground_stations_source="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        )

    result = compute_coverage_preview(
        constellation_source="nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
        satellite_type_override="leo-relay",
        ground_stations_source="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    )
    assert result.orbital_period_s > 0


def test_preview_uses_historical_starlink_576_cross_plane_mesh() -> None:
    custom_constellation = {
        "constellation": {
            "id": "custom-48x12-550km",
            "display_name": "Custom 48x12 550 km shell",
            "node": "nodalarc:nodes/space/starlink-v2-mesh.yaml",
            "orbit": {
                "orbit": {
                    "id": "custom-48x12-550km-orbit-550km-53deg",
                    "central_body": "nodalarc:bodies/earth.yaml",
                    "epoch": "2026-06-08T00:00:00Z",
                    "shape": {"altitude_km": 550},
                    "orientation": {
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "argument_of_perigee_deg": 0,
                    },
                    "phase": {"mean_anomaly_deg": 0},
                    "propagator": "j2_mean_elements",
                    "reference": "user-authored",
                }
            },
            "planes": {"count": 48, "raan_spacing_deg": 7.5},
            "slots_per_plane": 12,
            "phasing": {"mode": "walker_delta", "phase_offset_deg": 0.625},
            "node_tags": [{"tag": "all"}],
            "reference": "user-authored",
        }
    }

    topology = generated_isl_topology(custom_constellation)
    assert topology is not None
    assert topology["mode"] == "explicit_pairs"
    assert len(topology["pairs"]) == 1152

    session = _preview_segment_session(
        constellation_source=custom_constellation,
        ground_stations_source="nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        isl_topology=topology,
    )
    resolved = resolve_session_with_assets(session).resolved
    runtime = build_ome_inputs_from_resolved(resolved)
    summary = topology_summary(runtime.neighbors)

    assert summary["has_cross_plane"] is True
    assert summary["max_cross_per_sat"] == 2
    assert summary["total_unique_pairs"] == 1152
