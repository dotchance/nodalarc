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
from nodalarc.models.coverage import CoveragePreviewResult
from ome.coverage_preview import compute_coverage_preview


@pytest.fixture(scope="module")
def demo_preview() -> CoveragePreviewResult:
    """Run coverage preview once with the smallest real config."""
    return compute_coverage_preview(
        constellation_source="configs/constellations/demo-36.yaml",
        satellite_type_override=None,
        ground_stations_source="configs/ground-stations/sets/demo.yaml",
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


def test_warnings_is_list(demo_preview):
    assert isinstance(demo_preview.warnings, list)


# --- Error cases ---


def test_missing_constellation_raises():
    with pytest.raises(ValueError, match="constellation is required"):
        compute_coverage_preview(None, None, "configs/ground-stations/sets/demo.yaml")


def test_missing_ground_stations_raises():
    with pytest.raises(ValueError, match="ground_stations is required"):
        compute_coverage_preview("configs/constellations/demo-36.yaml", None, None)


def test_nonexistent_constellation_raises():
    with pytest.raises(FileNotFoundError):
        compute_coverage_preview(
            "nonexistent-constellation", None, "configs/ground-stations/sets/demo.yaml"
        )


def test_nonexistent_ground_stations_raises():
    with pytest.raises(FileNotFoundError):
        compute_coverage_preview("configs/constellations/demo-36.yaml", None, "nonexistent-gs-set")


def test_satellite_type_override():
    """Verify that satellite type override doesn't crash the pipeline."""
    result = compute_coverage_preview(
        constellation_source="configs/constellations/demo-36.yaml",
        satellite_type_override="starlink-v2",
        ground_stations_source="configs/ground-stations/sets/demo.yaml",
    )
    assert isinstance(result, CoveragePreviewResult)
    assert result.isl.total_possible > 0
