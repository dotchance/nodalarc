# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for PositionTable with SessionEphemeris-based local propagation."""

from __future__ import annotations

from datetime import UTC, datetime

from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    SessionEphemeris,
)
from scheduler.latency_model import PositionTable

from tests.physics_fixtures import EARTH_TEST_EPHEMERIS_BODY_FRAMES

EPOCH = 1735689600.0  # 2025-01-01T00:00:00 UTC


def _keplerian_node(**overrides) -> EphemerisNodeKeplerian:
    data = {
        "propagator": "two-body",
        "semi_major_axis_km": 6921.0,
        "eccentricity": 0.0,
        "inclination_deg": 53.0,
        "raan_deg": 0.0,
        "argument_of_perigee_deg": 0.0,
        "mean_anomaly_deg": 0.0,
        "plane": 0,
        "slot": 0,
        "reference_body": "earth",
        "frame_id": "earth",
    }
    data.update(overrides)
    return EphemerisNodeKeplerian(**data)


def _make_ephemeris() -> SessionEphemeris:
    return SessionEphemeris(
        epoch_id=0,
        sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        epoch_unix=EPOCH,
        body_frames=EARTH_TEST_EPHEMERIS_BODY_FRAMES,
        nodes={
            "sat-P00S00": _keplerian_node(),
            "sat-P00S01": _keplerian_node(mean_anomaly_deg=32.7, slot=1),
            "gs-ashburn": EphemerisNodeFixed(
                lat_deg=39.04,
                lon_deg=-77.49,
                alt_km=0.095,
                reference_body="earth",
                frame_id="earth",
            ),
        },
    )


class TestLoadEphemeris:
    def test_load_clears_previous(self):
        pt = PositionTable()
        pt.load_ephemeris(_make_ephemeris())
        # Load a different ephemeris with only one node
        eph2 = SessionEphemeris(
            epoch_id=1,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=EPOCH,
            body_frames=EARTH_TEST_EPHEMERIS_BODY_FRAMES,
            nodes={
                "sat-P00S00": _keplerian_node(),
            },
        )
        pt.load_ephemeris(eph2)
        # sat-P00S01 should no longer be resolvable
        assert pt.compute_link_latency("sat-P00S01", "gs-ashburn", EPOCH) is None


class TestComputeLinkLatency:
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
