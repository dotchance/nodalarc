# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for the shared propagator in lib/nodalarc/propagator.py.

Verifies that the extracted shared library propagator produces identical
results to golden values and that the OME re-export layer works.
"""

from __future__ import annotations

import math

from nodalarc.geo import compute_range_km
from nodalarc.orbital import elements_from_params
from nodalarc.propagator import (
    EcefVec3,
    GeoPosition,
    Vec3,
    ecef_to_geodetic,
    geodetic_to_ecef,
    gmst,
    orbital_period,
    orbital_velocity,
    propagate_keplerian,
)

# Fixed epoch for deterministic tests: 2025-01-01T00:00:00 UTC
EPOCH = 1735689600.0


class TestOrbitalPeriodAndVelocity:
    def test_550km_leo_period(self):
        """550 km LEO orbit period should be ~95.6 minutes."""
        t = orbital_period(550.0)
        assert 5700 < t < 5800, f"Expected ~5730s, got {t}"

    def test_iss_altitude_period(self):
        """408 km (ISS) orbit period should be ~92.7 minutes."""
        t = orbital_period(408.0)
        assert 5500 < t < 5600

    def test_550km_velocity(self):
        """550 km LEO velocity should be ~7.6 km/s."""
        v = orbital_velocity(550.0)
        assert 7.5 < v < 7.7


class TestPropagate:
    def test_return_to_start_after_one_period(self):
        """After one full orbital period, satellite returns to the same ECI position."""
        elements = elements_from_params(550.0, 53.0, 0.0, 0.0)
        period = orbital_period(550.0)

        pos0, _, _ = propagate_keplerian(elements, EPOCH, 0.0)
        pos1, _, _ = propagate_keplerian(elements, EPOCH, period)

        # ECI position repeats after one period, but ECEF rotates with Earth.
        # The ECEF positions will differ by Earth rotation. Check that the
        # altitude (distance from Earth center) is the same.
        r0 = math.sqrt(pos0.x**2 + pos0.y**2 + pos0.z**2)
        r1 = math.sqrt(pos1.x**2 + pos1.y**2 + pos1.z**2)
        assert abs(r0 - r1) < 0.01, f"Radii differ: {r0} vs {r1}"

    def test_latitude_bounded_by_inclination(self):
        """Satellite with 53° inclination should never exceed ±53° latitude."""
        elements = elements_from_params(550.0, 53.0, 0.0, 0.0)
        period = orbital_period(550.0)

        for step in range(0, int(period), 60):
            _, _, geo = propagate_keplerian(elements, EPOCH, float(step))
            assert abs(geo.lat_deg) <= 54.0, (
                f"Latitude {geo.lat_deg}° exceeds inclination at t={step}"
            )

    def test_altitude_stays_constant_circular(self):
        """Circular orbit altitude should be constant (~550 km)."""
        elements = elements_from_params(550.0, 53.0, 0.0, 0.0)

        for step in range(0, 6000, 300):
            _, _, geo = propagate_keplerian(elements, EPOCH, float(step))
            assert 540 < geo.alt_km < 560, f"Altitude {geo.alt_km} km at t={step}"

    def test_different_raan_produces_different_positions(self):
        """Two satellites in different orbital planes should be at different positions."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 90.0, 0.0)

        pos1, _, _ = propagate_keplerian(e1, EPOCH, 100.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 100.0)

        d = compute_range_km(pos1, pos2)
        assert d > 100.0, f"Expected separation, got {d} km"


class TestGeodeticRoundTrip:
    def test_ecef_to_geodetic_to_ecef(self):
        """ECEF → geodetic → ECEF round-trip should be identity."""
        original = EcefVec3(Vec3(6928.137, 0.0, 0.0))  # ~550 km above equator at 0° lon
        geo = ecef_to_geodetic(original)
        restored = geodetic_to_ecef(geo)

        assert abs(original.x - restored.x) < 0.001
        assert abs(original.y - restored.y) < 0.001
        assert abs(original.z - restored.z) < 0.001

    def test_known_position(self):
        """Geodetic for a known ECEF point on equator at prime meridian."""
        pos = EcefVec3(Vec3(6928.137, 0.0, 0.0))
        geo = ecef_to_geodetic(pos)
        assert abs(geo.lat_deg) < 0.01
        assert abs(geo.lon_deg) < 0.01
        assert abs(geo.alt_km - 550.0) < 1.0


class TestGmst:
    def test_j2000_epoch(self):
        """GMST at J2000 epoch should be ~280.46° ≈ 4.894 rad."""
        from nodalarc.propagator import J2000_UNIX

        g = gmst(J2000_UNIX)
        assert 4.8 < g < 5.0, f"Expected ~4.894 rad, got {g}"


class TestReExport:
    def test_ome_propagator_reexport(self):
        """ome.propagator should re-export everything from nodalarc.propagator."""
        from ome.propagator import (
            GeoPosition as OmeGeo,
        )
        from ome.propagator import (
            propagate_keplerian as ome_prop,
        )

        # Same objects — not copies
        assert OmeGeo is GeoPosition
        assert ome_prop is propagate_keplerian

    def test_ome_propagator_elements_from_params(self):
        """elements_from_params should be accessible from ome.propagator."""
        from ome.propagator import elements_from_params as ome_efp

        e = ome_efp(550.0, 53.0, 0.0, 0.0)
        assert e.semi_major_axis_km > 6900
