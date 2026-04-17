"""Terminal-bandwidth resolution tests (R-TO-003).

Verifies that satellite-type and ground-station terminal bandwidth fields
flow through `isl_terminal_bandwidth_mbps`, `satellite_ground_bandwidth_mbps`,
`gs_terminal_bandwidth_mbps`, `isl_link_bandwidth_mbps`, and
`ground_link_bandwidth_mbps` to produce correct per-pair bandwidth values.

These helpers replace the previously hardcoded 1000 Mbps in
services/scheduler/__main__.py._build_interface_map.
"""

from __future__ import annotations

import pytest
from nodalarc.constellation_loader import (
    ground_link_bandwidth_mbps,
    gs_terminal_bandwidth_mbps,
    isl_link_bandwidth_mbps,
    isl_terminal_bandwidth_mbps,
    load_constellation,
    load_ground_stations,
    satellite_ground_bandwidth_mbps,
)
from nodalarc.models.constellation import (
    GroundTerminal,
    IslTerminal,
)
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
)
from nodalarc.models.satellite_type import (
    GroundTerminalDef as SatGroundTerminalDef,
)
from nodalarc.models.satellite_type import (
    IslTerminalDef,
)

from tests.conftest import CONFIGS_DIR


class TestIslTerminalBandwidthLookup:
    """Lookup across consecutive terminal blocks indexed by islN."""

    def test_single_block(self):
        terminals = [
            IslTerminal(
                type="optical",
                count=4,
                max_range_km=5000,
                bandwidth_mbps=100.0,
                max_tracking_rate_deg_s=3.0,
            )
        ]
        assert isl_terminal_bandwidth_mbps(terminals, "isl0") == 100.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl1") == 100.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl2") == 100.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl3") == 100.0

    def test_multi_block_index_spans_blocks(self):
        """[optical count=2, rf count=2] → isl0/isl1 optical, isl2/isl3 rf."""
        terminals = [
            IslTerminal(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100_000.0,
                max_tracking_rate_deg_s=3.0,
            ),
            IslTerminal(
                type="rf",
                count=2,
                max_range_km=3000,
                bandwidth_mbps=10_000.0,
                max_tracking_rate_deg_s=5.0,
            ),
        ]
        assert isl_terminal_bandwidth_mbps(terminals, "isl0") == 100_000.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl1") == 100_000.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl2") == 10_000.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl3") == 10_000.0

    def test_out_of_range_raises(self):
        terminals = [
            IslTerminal(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100.0,
                max_tracking_rate_deg_s=3.0,
            )
        ]
        with pytest.raises(ValueError, match="out of range"):
            isl_terminal_bandwidth_mbps(terminals, "isl5")

    def test_invalid_interface_name_raises(self):
        terminals = [
            IslTerminal(
                type="optical",
                count=4,
                max_range_km=5000,
                bandwidth_mbps=100.0,
                max_tracking_rate_deg_s=3.0,
            )
        ]
        with pytest.raises(ValueError, match="Expected 'islN'"):
            isl_terminal_bandwidth_mbps(terminals, "gnd0")
        with pytest.raises(ValueError, match="Invalid ISL interface"):
            isl_terminal_bandwidth_mbps(terminals, "islx")

    def test_satellite_type_def_compatible(self):
        """Duck-type: satellite_type.IslTerminalDef must work interchangeably."""
        terminals = [
            IslTerminalDef(
                type="optical",
                count=3,
                max_range_km=5000,
                bandwidth_mbps=250.0,
                max_tracking_rate_deg_s=3.0,
            )
        ]
        assert isl_terminal_bandwidth_mbps(terminals, "isl0") == 250.0
        assert isl_terminal_bandwidth_mbps(terminals, "isl2") == 250.0


class TestSatelliteGroundBandwidth:
    def test_single_terminal(self):
        terminals = [GroundTerminal(type="rf", count=1, bandwidth_mbps=500.0)]
        assert satellite_ground_bandwidth_mbps(terminals) == 500.0

    def test_multiple_terminals_returns_min(self):
        terminals = [
            GroundTerminal(type="optical", count=1, bandwidth_mbps=10_000.0),
            GroundTerminal(type="rf", count=1, bandwidth_mbps=1_000.0),
        ]
        assert satellite_ground_bandwidth_mbps(terminals) == 1_000.0

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="no ground terminals"):
            satellite_ground_bandwidth_mbps([])

    def test_satellite_type_def_compatible(self):
        """Duck-type against satellite_type.GroundTerminalDef."""
        terminals = [SatGroundTerminalDef(type="rf", count=1, bandwidth_mbps=800.0)]
        assert satellite_ground_bandwidth_mbps(terminals) == 800.0


class TestGsTerminalBandwidth:
    def _file(self, station_terminals=None):
        stations = [
            GroundStationConfig(
                name="hawthorne",
                lat_deg=33.916,
                lon_deg=-118.333,
                terminals=station_terminals,
            ),
        ]
        return GroundStationFile(
            default_terminals=[
                GroundTerminalDef(type="rf", count=1, bandwidth_mbps=500.0, tracking_capacity=1),
            ],
            stations=stations,
        )

    def test_uses_default_when_station_has_none(self):
        gs_file = self._file(station_terminals=None)
        assert gs_terminal_bandwidth_mbps(gs_file, "hawthorne") == 500.0

    def test_station_override_takes_precedence(self):
        gs_file = self._file(
            station_terminals=[
                GroundTerminalDef(
                    type="optical", count=1, bandwidth_mbps=10_000.0, tracking_capacity=1
                ),
            ]
        )
        assert gs_terminal_bandwidth_mbps(gs_file, "hawthorne") == 10_000.0

    def test_unknown_station_raises(self):
        gs_file = self._file()
        with pytest.raises(ValueError, match="not found"):
            gs_terminal_bandwidth_mbps(gs_file, "nonexistent")

    def test_min_across_station_terminals(self):
        gs_file = self._file(
            station_terminals=[
                GroundTerminalDef(
                    type="optical", count=1, bandwidth_mbps=10_000.0, tracking_capacity=1
                ),
                GroundTerminalDef(type="rf", count=1, bandwidth_mbps=500.0, tracking_capacity=1),
            ]
        )
        assert gs_terminal_bandwidth_mbps(gs_file, "hawthorne") == 500.0


class TestIslLinkBandwidthRealConfig:
    """End-to-end: load a real constellation and verify link bandwidth."""

    def test_starlink_early_uniform(self):
        """starlink-early-44 uses starlink-v2 (100 Mbps optical ISL)."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        # All satellites use starlink-v2 (4 optical @ 100 Mbps) → all ISL pairs 100 Mbps
        bw = isl_link_bandwidth_mbps(config, 0, 0, "isl0", 0, 1, "isl1")
        assert bw == 100.0
        bw2 = isl_link_bandwidth_mbps(config, 1, 5, "isl2", 2, 3, "isl3")
        assert bw2 == 100.0


class TestGroundLinkBandwidthRealConfig:
    def test_starlink_early_hawthorne_ground(self):
        """starlink-v2 sat ground terminal = 1000 Mbps; typical GS default varies."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        gs_file = load_ground_stations("configs/ground-stations/sets/demo.yaml")
        # Sat bw: starlink-v2 ground_terminals → 1000 Mbps
        # GS bw: whatever demo ground stations declare; take min
        bw = ground_link_bandwidth_mbps(config, gs_file, 0, 0, "hawthorne")
        assert bw > 0
        assert bw <= 1000.0  # sat side caps at 1000


class TestBandwidthNotHardcoded:
    """Regression guard: the fix for the 1000.0 hardcode should use real values."""

    def test_starlink_isl_is_not_1000_mbps(self):
        """If someone regresses the fix to hardcode 1000, this test fails."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        bw = isl_link_bandwidth_mbps(config, 0, 0, "isl0", 0, 1, "isl0")
        # starlink-v2 is 100 Mbps — must NOT be the old 1000.0 default
        assert bw == 100.0
        assert bw != 1000.0
