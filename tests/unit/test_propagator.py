"""Test Keplerian propagator — orbital mechanics validation.

Validates against analytic properties:
- Period, velocity, inclination bounds
- Return to start after one period
- ECEF↔geodetic round-trip
"""

import math

import pytest
from nodalarc.constants import EARTH_RADIUS_KM
from ome.propagator import (
    GeoPosition,
    Vec3,
    distance_km,
    ecef_to_geodetic,
    eci_to_ecef_velocity,
    elements_from_params,
    geodetic_to_ecef,
    j2_circular_secular_rates,
    orbital_period,
    orbital_velocity,
    propagate_eci,
    propagate_j2_mean_elements,
    propagate_keplerian,
)

# Reference epoch: 2025-01-01T00:00:00 UTC
EPOCH = 1735689600.0


@pytest.fixture
def iss_like_elements():
    """ISS-like orbit: 408 km, 51.6° inclination."""
    return elements_from_params(408.0, 51.6, 0.0, 0.0)


@pytest.fixture
def starlink_elements():
    """Starlink-like orbit: 550 km, 53° inclination."""
    return elements_from_params(550.0, 53.0, 0.0, 0.0)


class TestOrbitalPeriod:
    def test_550km_period(self):
        period = orbital_period(550.0)
        # Expected ~5754 seconds (95.9 minutes)
        assert 5700.0 < period < 5800.0

    def test_408km_period(self):
        period = orbital_period(408.0)
        # ISS: ~92.7 minutes = ~5562 seconds
        assert 5500.0 < period < 5620.0

    def test_geostationary_period(self):
        # GEO: ~35786 km altitude, period ~86164 seconds (23h 56m 4s)
        period = orbital_period(35786.0)
        assert 86000.0 < period < 86400.0


class TestOrbitalVelocity:
    def test_550km_velocity(self):
        v = orbital_velocity(550.0)
        # Expected ~7.59 km/s
        assert 7.5 < v < 7.7

    def test_408km_velocity(self):
        v = orbital_velocity(408.0)
        # Expected ~7.66 km/s
        assert 7.6 < v < 7.8


class TestReturnToStart:
    def test_satellite_returns_after_one_period_eci(self, starlink_elements):
        """Satellite returns to starting position after one period (ECI frame)."""
        period = orbital_period(550.0)
        pos_start, _ = propagate_eci(starlink_elements, 0.0)
        pos_end, _ = propagate_eci(starlink_elements, period)
        dist = distance_km(pos_start, pos_end)
        # Should be < 1 km (circular orbit, exact return in ECI)
        assert dist < 0.01, f"ECI return error: {dist} km"

    def test_velocity_magnitude_constant(self, starlink_elements):
        """Velocity magnitude is constant for circular orbit."""
        _, vel_0 = propagate_eci(starlink_elements, 0.0)
        _, vel_half = propagate_eci(starlink_elements, orbital_period(550.0) / 2)
        _, vel_quarter = propagate_eci(starlink_elements, orbital_period(550.0) / 4)

        v0 = math.sqrt(vel_0.x**2 + vel_0.y**2 + vel_0.z**2)
        v_half = math.sqrt(vel_half.x**2 + vel_half.y**2 + vel_half.z**2)
        v_quarter = math.sqrt(vel_quarter.x**2 + vel_quarter.y**2 + vel_quarter.z**2)

        assert abs(v0 - v_half) < 0.001
        assert abs(v0 - v_quarter) < 0.001


class TestJ2MeanElementPropagation:
    def test_j2_matches_keplerian_at_epoch(self, starlink_elements):
        pos_k, vel_k, geo_k = propagate_keplerian(starlink_elements, EPOCH, 0.0)
        pos_j2, vel_j2, geo_j2 = propagate_j2_mean_elements(starlink_elements, EPOCH, 0.0)

        assert distance_km(pos_k, pos_j2) < 1e-9
        assert distance_km(vel_k, vel_j2) < 0.02
        assert abs(geo_k.lat_deg - geo_j2.lat_deg) < 1e-9
        assert abs(geo_k.lon_deg - geo_j2.lon_deg) < 1e-9

    def test_j2_raan_precession_rate_is_physical_for_starlink_shell(self, starlink_elements):
        raan_dot, mean_anomaly_dot = j2_circular_secular_rates(starlink_elements)
        raan_deg_per_day = math.degrees(raan_dot) * 86400.0
        mean_motion_deg_per_s = math.degrees(mean_anomaly_dot)

        assert -5.0 < raan_deg_per_day < -4.0
        assert mean_motion_deg_per_s > 0.0

    def test_j2_diverges_from_circular_keplerian_over_one_day(self, starlink_elements):
        pos_k, _, _ = propagate_keplerian(starlink_elements, EPOCH, 86400.0)
        pos_j2, vel_j2, _ = propagate_j2_mean_elements(starlink_elements, EPOCH, 86400.0)

        assert distance_km(pos_k, pos_j2) > 100.0
        assert math.sqrt(vel_j2.x**2 + vel_j2.y**2 + vel_j2.z**2) > 7.0


class TestInclinationBounds:
    def test_latitude_bounded_by_inclination(self, starlink_elements):
        """Ground track latitude should be bounded by inclination (53°)."""
        period = orbital_period(550.0)
        max_lat = 0.0
        steps = 100
        for step in range(steps):
            dt = step * period / steps
            _, _, geo = propagate_keplerian(starlink_elements, EPOCH, dt)
            max_lat = max(max_lat, abs(geo.lat_deg))
        # Max latitude should be close to inclination (53°)
        assert 52.0 < max_lat < 54.0

    def test_polar_orbit_reaches_poles(self):
        """97.4° inclination orbit should reach near-polar latitudes."""
        elements = elements_from_params(550.0, 97.4, 0.0, 0.0)
        period = orbital_period(550.0)
        max_lat = 0.0
        steps = 100
        for step in range(steps):
            dt = step * period / steps
            _, _, geo = propagate_keplerian(elements, EPOCH, dt)
            max_lat = max(max_lat, abs(geo.lat_deg))
        # Should reach near 82.6° (180 - 97.4)
        assert max_lat > 80.0


class TestECEFGeodeticRoundTrip:
    def test_round_trip_equator(self):
        original = GeoPosition(lat_deg=0.0, lon_deg=0.0, alt_km=550.0)
        ecef = geodetic_to_ecef(original)
        recovered = ecef_to_geodetic(ecef)
        assert abs(recovered.lat_deg - original.lat_deg) < 0.001
        assert abs(recovered.lon_deg - original.lon_deg) < 0.001
        assert abs(recovered.alt_km - original.alt_km) < 0.01

    def test_round_trip_pole(self):
        original = GeoPosition(lat_deg=90.0, lon_deg=0.0, alt_km=550.0)
        ecef = geodetic_to_ecef(original)
        recovered = ecef_to_geodetic(ecef)
        assert abs(recovered.lat_deg - 90.0) < 0.001
        assert abs(recovered.alt_km - 550.0) < 0.01

    def test_round_trip_mid_latitude(self):
        original = GeoPosition(lat_deg=45.0, lon_deg=-120.0, alt_km=408.0)
        ecef = geodetic_to_ecef(original)
        recovered = ecef_to_geodetic(ecef)
        assert abs(recovered.lat_deg - original.lat_deg) < 0.001
        assert abs(recovered.lon_deg - original.lon_deg) < 0.001
        assert abs(recovered.alt_km - original.alt_km) < 0.01

    def test_round_trip_southern_hemisphere(self):
        original = GeoPosition(lat_deg=-33.87, lon_deg=151.21, alt_km=0.058)
        ecef = geodetic_to_ecef(original)
        recovered = ecef_to_geodetic(ecef)
        assert abs(recovered.lat_deg - original.lat_deg) < 0.001
        assert abs(recovered.lon_deg - original.lon_deg) < 0.001
        assert abs(recovered.alt_km - original.alt_km) < 0.1


class TestAltitude:
    def test_propagated_altitude_near_target(self, starlink_elements):
        """Propagated position should maintain ~550 km altitude."""
        _, _, geo = propagate_keplerian(starlink_elements, EPOCH, 0.0)
        # Altitude should be close to 550 km (within a few km due to WGS84 vs spherical)
        assert 540.0 < geo.alt_km < 560.0

    def test_altitude_constant_over_orbit(self, starlink_elements):
        """Altitude should be nearly constant for circular orbit."""
        period = orbital_period(550.0)
        alts = []
        for step in range(20):
            dt = step * period / 20
            _, _, geo = propagate_keplerian(starlink_elements, EPOCH, dt)
            alts.append(geo.alt_km)
        alt_range = max(alts) - min(alts)
        # Due to WGS84 oblateness, altitude varies slightly but should be < 25 km
        assert alt_range < 25.0


class TestDistance:
    def test_distance_same_point(self):
        a = Vec3(1000.0, 2000.0, 3000.0)
        assert distance_km(a, a) == 0.0

    def test_distance_known(self):
        a = Vec3(0.0, 0.0, 0.0)
        b = Vec3(3.0, 4.0, 0.0)
        assert abs(distance_km(a, b) - 5.0) < 1e-10


class TestMultipleSatellites:
    def test_different_raan_different_positions(self):
        """Two satellites with different RAAN should be at different positions."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 30.0, 0.0)
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        dist = distance_km(pos1, pos2)
        assert dist > 100.0  # Should be well separated

    def test_same_plane_different_anomaly(self):
        """Two satellites in same plane, different true anomaly."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 180.0)
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        dist = distance_km(pos1, pos2)
        # Opposite sides: should be ~2 * (R + alt) = ~13842 km
        expected = 2 * (EARTH_RADIUS_KM + 550.0)
        assert abs(dist - expected) < 10.0


class TestEcefVelocity:
    """Verify ECEF velocity differs from ECI by Earth rotation contribution."""

    def test_ecef_velocity_differs_from_eci(self):
        """For equatorial orbit, ECEF speed differs from ECI by ~0.465 km/s."""
        elements = elements_from_params(550.0, 0.0, 0.0, 0.0)
        pos_eci, vel_eci = propagate_eci(elements, 0.0)
        vel_ecef = eci_to_ecef_velocity(pos_eci, vel_eci, EPOCH)

        eci_speed = math.sqrt(vel_eci.x**2 + vel_eci.y**2 + vel_eci.z**2)
        ecef_speed = math.sqrt(vel_ecef.x**2 + vel_ecef.y**2 + vel_ecef.z**2)

        # Earth surface rotation speed at equator: ~0.465 km/s
        # ECEF speed should differ from ECI speed
        assert abs(eci_speed - ecef_speed) > 0.3
        assert abs(eci_speed - ecef_speed) < 0.6

    def test_propagate_returns_ecef_velocity(self):
        """propagate_keplerian now returns ECEF velocity, not ECI."""
        elements = elements_from_params(550.0, 53.0, 0.0, 0.0)
        pos_ecef, vel_ecef, geo = propagate_keplerian(elements, EPOCH, 0.0)
        _, vel_eci = propagate_eci(elements, 0.0)

        # Velocities should differ (ECEF != ECI)
        diff = math.sqrt(
            (vel_ecef.x - vel_eci.x) ** 2
            + (vel_ecef.y - vel_eci.y) ** 2
            + (vel_ecef.z - vel_eci.z) ** 2,
        )
        assert diff > 0.1  # Non-trivial difference

    def test_velocity_predicts_next_position(self):
        """pos(t+dt) ≈ pos(t) + vel(t)*dt for small dt."""
        elements = elements_from_params(550.0, 53.0, 30.0, 45.0)
        dt_step = 1.0  # 1 second

        pos0, vel0, _ = propagate_keplerian(elements, EPOCH, 0.0)
        pos1, _, _ = propagate_keplerian(elements, EPOCH, dt_step)

        # Linear prediction
        pred = Vec3(
            pos0.x + vel0.x * dt_step,
            pos0.y + vel0.y * dt_step,
            pos0.z + vel0.z * dt_step,
        )

        # Prediction error should be small for 1s step
        err = distance_km(pred, pos1)
        assert err < 0.1  # Less than 100m error for 1s step
