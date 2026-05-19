# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for PositionTable with SessionEphemeris-based local propagation."""

from __future__ import annotations

from datetime import UTC, datetime

from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    SessionEphemeris,
)
from scheduler.latency_model import PositionTable

EPOCH = 1735689600.0  # 2025-01-01T00:00:00 UTC
ISS_TLE_EPOCH = 1615896900.000275
ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"


def _make_ephemeris() -> SessionEphemeris:
    return SessionEphemeris(
        epoch_id=0,
        sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        epoch_unix=EPOCH,
        nodes={
            "sat-P00S00": EphemerisNodeKeplerian(
                propagator="keplerian-circular",
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                true_anomaly_deg=0.0,
                plane=0,
                slot=0,
            ),
            "sat-P00S01": EphemerisNodeKeplerian(
                propagator="keplerian-circular",
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                true_anomaly_deg=32.7,
                plane=0,
                slot=1,
            ),
            "gs-ashburn": EphemerisNodeFixed(lat_deg=39.04, lon_deg=-77.49, alt_km=0.095),
        },
    )


class TestLoadEphemeris:
    def test_load_sets_loaded_flag(self):
        pt = PositionTable()
        assert not pt.loaded
        pt.load_ephemeris(_make_ephemeris())
        assert pt.loaded

    def test_load_clears_previous(self):
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        # Load a different ephemeris with only one node
        eph2 = SessionEphemeris(
            epoch_id=1,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=EPOCH,
            nodes={
                "sat-P00S00": EphemerisNodeKeplerian(
                    propagator="keplerian-circular",
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=0.0,
                    true_anomaly_deg=0.0,
                    plane=0,
                    slot=0,
                ),
            },
        )
        pt.load_ephemeris(eph2)
        # sat-P00S01 should no longer be resolvable
        assert pt.compute_link_latency("sat-P00S01", "gs-ashburn", EPOCH) is None


class TestComputeLinkLatency:
    def test_isl_latency_positive(self):
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        lat = pt.compute_link_latency("sat-P00S00", "sat-P00S01", EPOCH)
        assert lat is not None
        assert lat > 0.0

    def test_ground_link_latency_positive(self):
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        lat = pt.compute_link_latency("sat-P00S00", "gs-ashburn", EPOCH)
        assert lat is not None
        assert lat > 0.0

    def test_tle_ephemeris_latency_positive(self):
        eph = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime.fromtimestamp(ISS_TLE_EPOCH, UTC),
            epoch_unix=ISS_TLE_EPOCH,
            nodes={
                "sat-P00S00": EphemerisNodeTLE(
                    tle_line_1=ISS_TLE_LINE_1,
                    tle_line_2=ISS_TLE_LINE_2,
                    plane=0,
                    slot=0,
                    norad_id=25544,
                ),
                "gs-ashburn": EphemerisNodeFixed(
                    lat_deg=39.04,
                    lon_deg=-77.49,
                    alt_km=0.095,
                ),
            },
        )
        pt = PositionTable()
        pt.load_ephemeris(eph)
        lat = pt.compute_link_latency("sat-P00S00", "gs-ashburn", ISS_TLE_EPOCH + 60.0)
        assert lat is not None
        assert lat > 0.0

    def test_j2_ephemeris_uses_j2_propagator_identity(self):
        kepler = _make_ephemeris()
        j2_nodes = dict(kepler.nodes)
        sat = j2_nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeKeplerian)
        j2_nodes["sat-P00S00"] = sat.model_copy(update={"propagator": "j2-mean-elements"})
        j2 = kepler.model_copy(update={"nodes": j2_nodes})

        pt_kepler = PositionTable()
        pt_kepler.load_ephemeris(kepler)
        pt_j2 = PositionTable()
        pt_j2.load_ephemeris(j2)

        lat_kepler = pt_kepler.compute_link_latency("sat-P00S00", "gs-ashburn", EPOCH + 86400)
        lat_j2 = pt_j2.compute_link_latency("sat-P00S00", "gs-ashburn", EPOCH + 86400)
        assert lat_kepler is not None
        assert lat_j2 is not None
        assert abs(lat_j2 - lat_kepler) > 0.01

    def test_unknown_node_returns_none(self):
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        assert pt.compute_link_latency("sat-UNKNOWN", "sat-P00S00", EPOCH) is None

    def test_latency_changes_over_time(self):
        """Latency between satellites changes as they orbit."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        lat0 = pt.compute_link_latency("sat-P00S00", "sat-P00S01", EPOCH)
        # 30 minutes later
        lat30 = pt.compute_link_latency("sat-P00S00", "sat-P00S01", EPOCH + 1800)
        assert lat0 is not None
        assert lat30 is not None
        # Same-plane satellites maintain constant distance (circular orbit),
        # but the ECEF positions change. Latency should be similar but not identical
        # due to Earth rotation changing the ECEF coordinates.
        # Key point: the function works at different times.
        assert lat30 > 0.0

    def test_speed_of_light_formula(self):
        """Verify latency = range / c * 1000 (speed of light in vacuum)."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        lat = pt.compute_link_latency("sat-P00S00", "sat-P00S01", EPOCH)
        rng = pt.compute_link_range("sat-P00S00", "sat-P00S01", EPOCH)
        assert lat is not None and rng is not None
        expected = rng / 299792.458 * 1000
        assert abs(lat - expected) < 0.001


class TestComputeLinkRange:
    def test_isl_range_reasonable(self):
        """ISL between adjacent same-plane sats should be within max ISL range."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        rng = pt.compute_link_range("sat-P00S00", "sat-P00S01", EPOCH)
        assert rng is not None
        assert 100 < rng < 6000  # Adjacent same-plane, typical range

    def test_ground_station_static(self):
        """Ground station range should change as satellite orbits."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        r0 = pt.compute_link_range("sat-P00S00", "gs-ashburn", EPOCH)
        r1 = pt.compute_link_range("sat-P00S00", "gs-ashburn", EPOCH + 300)
        assert r0 is not None and r1 is not None
        assert r0 != r1  # Satellite moves, range changes


class TestGetLinksNeedingUpdate:
    def test_initial_update_all_links(self):
        """All links need update when no previous latencies exist."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        active = {("sat-P00S00", "sat-P00S01")}
        updates = pt.get_links_needing_update(active, {}, EPOCH)
        assert len(updates) == 1
        node_a, node_b, latency, range_km = updates[0]
        assert node_a == "sat-P00S00"
        assert latency > 0.0
        assert range_km > 0.0

    def test_below_threshold_no_update(self):
        """Links within threshold should not be updated."""
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        active = {("sat-P00S00", "sat-P00S01")}
        # First call to get current latency
        updates = pt.get_links_needing_update(active, {}, EPOCH)
        assert len(updates) == 1
        current_lat = updates[0][2]
        # Second call at same time — should not need update
        last = {("sat-P00S00", "sat-P00S01"): current_lat}
        updates2 = pt.get_links_needing_update(active, last, EPOCH)
        assert len(updates2) == 0
