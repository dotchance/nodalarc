"""Tests for field of regard visibility check (Work Stream 5).

Verifies:
- 360° FoR (omnidirectional) always passes regardless of geometry
- 120° FoR blocks links when angle from velocity axis exceeds 60°
- Same-plane adjacent satellites pass the check with 120° FoR
- 180° separation fails the check with 120° FoR
- Iridium-next (120° FoR) blocks more cross-plane links than generic-4isl (160° FoR)
"""

import math

import pytest

from ome.propagator import Vec3
from ome.visibility import check_field_of_regard, check_isl_visibility


# Orbital velocity at 550 km altitude (approx)
V_550 = 7.58  # km/s
# Orbital velocity at 780 km altitude (approx)
V_780 = 7.45  # km/s
# Earth radius + altitudes
R_550 = 6921.0  # km
R_780 = 7151.0  # km


def _polar_orbit_state(raan_deg: float, true_anomaly_deg: float, altitude_km: float = 550.0):
    """Compute ECEF position and velocity for a polar orbit (i=90°).

    For a polar orbit with RAAN Ω:
    - pos = (R+h)(cos(ν)cos(Ω), cos(ν)sin(Ω), sin(ν))
    - vel = v(-sin(ν)cos(Ω), -sin(ν)sin(Ω), cos(ν))
    where ν = true anomaly.
    """
    R = 6371.0 + altitude_km
    v = math.sqrt(398600.4418 / R)  # vis-viva for circular orbit
    raan = math.radians(raan_deg)
    nu = math.radians(true_anomaly_deg)

    pos = Vec3(
        R * math.cos(nu) * math.cos(raan),
        R * math.cos(nu) * math.sin(raan),
        R * math.sin(nu),
    )
    vel = Vec3(
        v * (-math.sin(nu) * math.cos(raan)),
        v * (-math.sin(nu) * math.sin(raan)),
        v * math.cos(nu),
    )
    return pos, vel


class TestCheckFieldOfRegard:
    """Unit tests for check_field_of_regard()."""

    def test_omnidirectional_always_passes(self):
        """360° FoR should always pass regardless of geometry."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(90, 0)  # 90° RAAN offset
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 360.0) is True

    def test_same_plane_adjacent_feasible(self):
        """Two satellites in the same plane, adjacent slots (~32.7° apart).

        With 120° FoR, the angle from the velocity axis is small (~16°),
        so the link should be feasible.
        """
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(0, 32.7)  # Next slot in same plane
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 120.0) is True

    def test_same_plane_adjacent_aft_also_feasible(self):
        """The aft neighbor (looking backward) should also be feasible.

        With the min(angle, 180-angle) symmetry, both forward and aft
        neighbors have the same effective angle.
        """
        pos_a, vel_a = _polar_orbit_state(0, 32.7)
        pos_b, vel_b = _polar_orbit_state(0, 0)  # Previous slot (behind A)
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 120.0) is True

    def test_180_separation_infeasible(self):
        """Two satellites 180° apart: angle from velocity axis is 90°,
        which exceeds 60° (half of 120° FoR)."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(0, 180)  # Opposite side of orbit
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 120.0) is False

    def test_180_separation_feasible_with_large_for(self):
        """With 200° FoR (100° half-angle), 90° angle passes."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(0, 180)
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 200.0) is True

    def test_cross_plane_equator_blocked_for_narrow_for(self):
        """Cross-plane peer at the equator with large RAAN offset.

        At the equator for polar orbits, cross-plane LOS is perpendicular
        to velocity (90° from velocity axis). Blocked for FoR < 180°.
        """
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(31.6, 0)  # Adjacent plane at equator
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 120.0) is False

    def test_cross_plane_equator_also_blocked_for_160(self):
        """31.6° RAAN offset at equator gives ~90° from velocity axis.
        Still blocked even with 160° FoR (80° half-angle)."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(31.6, 0)
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 160.0) is False

    def test_zero_velocity_passes(self):
        """Edge case: zero velocity should not crash, returns True."""
        pos_a = Vec3(6921, 0, 0)
        vel_a = Vec3(0, 0, 0)
        pos_b = Vec3(0, 6921, 0)
        vel_b = Vec3(0, 0, 7.58)
        assert check_field_of_regard(pos_a, vel_a, pos_b, vel_b, 120.0) is True

    def test_same_position_passes(self):
        """Edge case: identical positions should not crash."""
        pos = Vec3(6921, 0, 0)
        vel = Vec3(0, 0, 7.58)
        assert check_field_of_regard(pos, vel, pos, vel, 120.0) is True


class TestFieldOfRegardInPipeline:
    """FoR check integrated into check_isl_visibility()."""

    def test_for_blocks_in_full_pipeline(self):
        """180° separation should be blocked by FoR in the full pipeline."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(0, 180)
        result = check_isl_visibility(
            pos_a, vel_a, pos_b, vel_b,
            max_range_km=50000.0,  # Very large to not trigger range check
            field_of_regard_deg=120.0,
        )
        # This will actually be blocked by LOS (goes through Earth), not FoR
        # So the reason might be "los_blocked" rather than "field_of_regard"
        assert result.visible is False

    def test_for_blocks_cross_plane_in_pipeline(self):
        """Cross-plane at equator blocked by FoR (90° from velocity axis)."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(31.6, 0)
        result = check_isl_visibility(
            pos_a, vel_a, pos_b, vel_b,
            max_range_km=50000.0,
            field_of_regard_deg=120.0,
        )
        assert result.visible is False
        assert result.reason == "field_of_regard"

    def test_adjacent_passes_in_pipeline(self):
        """Same-plane adjacent passes FoR check in the full pipeline."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(0, 32.7)
        result = check_isl_visibility(
            pos_a, vel_a, pos_b, vel_b,
            max_range_km=50000.0,
            field_of_regard_deg=120.0,
        )
        assert result.visible is True

    def test_default_360_skips_for_check(self):
        """Default 360° FoR should not block anything."""
        pos_a, vel_a = _polar_orbit_state(0, 0)
        pos_b, vel_b = _polar_orbit_state(31.6, 0)
        result = check_isl_visibility(
            pos_a, vel_a, pos_b, vel_b,
            max_range_km=50000.0,
            # field_of_regard_deg defaults to 360.0
        )
        # Should pass FoR (360°), might fail on other checks
        assert result.reason != "field_of_regard"


class TestFieldOfRegardComparison:
    """Compare visibility counts between different FoR values.

    Verifies that narrower FoR blocks more cross-plane links at high latitudes.
    """

    def _count_feasible_cross_plane_links(self, field_of_regard_deg: float, latitude_deg: float):
        """Count how many cross-plane links pass FoR check at a given latitude.

        Tests 6 orbital planes (like Iridium, RAAN spacing 31.6°) with one
        satellite per plane at the given latitude, checking all cross-plane pairs.
        """
        raan_spacing = 31.6
        n_planes = 6
        feasible = 0

        states = []
        for p in range(n_planes):
            pos, vel = _polar_orbit_state(p * raan_spacing, latitude_deg, altitude_km=780)
            states.append((pos, vel))

        for i in range(n_planes):
            for j in range(i + 1, n_planes):
                pos_a, vel_a = states[i]
                pos_b, vel_b = states[j]
                if check_field_of_regard(pos_a, vel_a, pos_b, vel_b, field_of_regard_deg):
                    feasible += 1

        return feasible

    def test_120_blocks_more_than_160_at_high_latitude(self):
        """At 60° latitude, 120° FoR should produce fewer feasible cross-plane
        links than 160° FoR."""
        feasible_120 = self._count_feasible_cross_plane_links(120.0, 60.0)
        feasible_160 = self._count_feasible_cross_plane_links(160.0, 60.0)
        assert feasible_120 < feasible_160, (
            f"120° FoR ({feasible_120} links) should block more than "
            f"160° FoR ({feasible_160} links) at 60° latitude"
        )

    def test_360_passes_all_at_any_latitude(self):
        """360° FoR should pass all cross-plane links at any latitude."""
        for lat in [0, 30, 60, 80]:
            feasible = self._count_feasible_cross_plane_links(360.0, lat)
            # 6 planes, all pairs = 6*5/2 = 15
            assert feasible == 15, f"360° FoR should pass all 15 pairs at {lat}° lat"

    def test_120_blocks_all_at_equator(self):
        """At the equator, all cross-plane LOS is perpendicular to velocity.
        120° FoR (60° half-angle) should block all cross-plane links."""
        feasible = self._count_feasible_cross_plane_links(120.0, 0.0)
        assert feasible == 0, f"120° FoR at equator: expected 0 feasible, got {feasible}"

    def test_narrower_for_always_blocks_at_least_as_many(self):
        """120° FoR should block at least as many links as 160° at every latitude."""
        for lat in [0, 30, 45, 60, 75]:
            f120 = self._count_feasible_cross_plane_links(120.0, lat)
            f160 = self._count_feasible_cross_plane_links(160.0, lat)
            assert f120 <= f160, (
                f"At {lat}° lat: 120° FoR ({f120}) should not exceed 160° ({f160})"
            )
