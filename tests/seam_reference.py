"""A small reference model for polar-seam encounter expectations, from declared data only.

Circular orbits from a constellation's declared altitude, inclination, RAAN
spacing, slot count and Walker-star phase offset, propagated in the inertial
frame with the first-order secular J2 rates that the declared
``j2_mean_elements`` propagator applies to a circular orbit: the node and the
mean anomaly drift, the argument of perigee does not. Independent of OME's
propagator and decision functions: it derives expected transition times and
rates for the seam experiments and never decides feasibility for the runtime.

Frame and units: positions in km and velocities in km/s in an Earth-centered
inertial frame; the line-of-sight rate is the angular speed of the
separation vector's direction in degrees per second. OME evaluates the same
quantity from Earth-fixed vectors; the rotating-frame term is at most
0.004 deg/s and is inside every tolerance the tests apply.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MU_EARTH_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.137
EARTH_J2 = 1.08262668e-3


@dataclass(frozen=True)
class DeclaredShell:
    """The declared parameters of a Walker-star shell, as the catalog states them."""

    altitude_km: float
    inclination_deg: float
    raan_spacing_deg: float
    slots_per_plane: int
    phase_offset_deg: float

    @property
    def semi_major_axis_km(self) -> float:
        return EARTH_RADIUS_KM + self.altitude_km

    def initial_elements(self, plane: int, slot: int) -> tuple[float, float, float, float]:
        """(a, inclination, RAAN, argument of latitude) in km and radians at epoch."""
        raan = math.radians(self.raan_spacing_deg * plane)
        u0 = math.radians(360.0 * slot / self.slots_per_plane + self.phase_offset_deg * plane)
        return self.semi_major_axis_km, math.radians(self.inclination_deg), raan, u0


def elements(
    shell: DeclaredShell, plane: int, slot: int, t_s: float
) -> tuple[float, float, float, float, float]:
    """(a, inclination, RAAN, argument of latitude, argument-of-latitude rate) of one
    satellite at t seconds after epoch, in km, radians and radians per second."""
    a, inc, raan0, u0 = shell.initial_elements(plane, slot)
    n = math.sqrt(MU_EARTH_KM3_S2 / a**3)
    k = 0.75 * n * EARTH_J2 * (EARTH_RADIUS_KM / a) ** 2
    raan_rate = -2.0 * k * math.cos(inc)
    u_rate = n + k * (3.0 * math.cos(inc) ** 2 - 1.0)
    return a, inc, raan0 + raan_rate * t_s, u0 + u_rate * t_s, u_rate


def state(
    shell: DeclaredShell, plane: int, slot: int, t_s: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Inertial position (km) and velocity (km/s) of one satellite at t seconds after epoch."""
    a, inc, raan, u, u_rate = elements(shell, plane, slot, t_s)
    cu, su, ci, si, co, so = (
        math.cos(u),
        math.sin(u),
        math.cos(inc),
        math.sin(inc),
        math.cos(raan),
        math.sin(raan),
    )
    position = (a * (cu * co - su * so * ci), a * (cu * so + su * co * ci), a * su * si)
    velocity = (
        a * u_rate * (-su * co - cu * so * ci),
        a * u_rate * (-su * so + cu * co * ci),
        a * u_rate * cu * si,
    )
    return position, velocity


def separation(pos_a, vel_a, pos_b, vel_b) -> tuple[float, float, bool]:
    """(range_km, line_of_sight_rate_deg_s, line_of_sight_clear) between two states."""
    rel = [pos_b[k] - pos_a[k] for k in range(3)]
    vel = [vel_b[k] - vel_a[k] for k in range(3)]
    range_km = math.sqrt(sum(x * x for x in rel))
    radial = sum(rel[k] * vel[k] for k in range(3)) / range_km
    transverse_sq = max(sum(x * x for x in vel) - radial * radial, 0.0)
    rate_deg_s = math.degrees(math.sqrt(transverse_sq) / range_km)
    # Line of sight: the closest point of the segment to Earth's centre is outside the sphere.
    dot_ad = sum(pos_a[k] * rel[k] for k in range(3))
    dot_dd = sum(x * x for x in rel)
    s = min(max(-dot_ad / dot_dd, 0.0), 1.0)
    closest = [pos_a[k] + s * rel[k] for k in range(3)]
    clear = math.sqrt(sum(x * x for x in closest)) > EARTH_RADIUS_KM
    return range_km, rate_deg_s, clear


def encounter(
    shell: DeclaredShell, a: tuple[int, int], b: tuple[int, int], t_s: float
) -> tuple[float, float, bool]:
    pos_a, vel_a = state(shell, *a, t_s)
    pos_b, vel_b = state(shell, *b, t_s)
    return separation(pos_a, vel_a, pos_b, vel_b)


def verdict(
    range_km: float, rate_deg_s: float, clear: bool, *, max_range_km: float, max_rate_deg_s: float
) -> str:
    """The physical verdict in the order the engine applies its gates."""
    if not clear:
        return "los_blocked"
    if range_km > max_range_km:
        return "range_exceeded"
    if rate_deg_s > max_rate_deg_s:
        return "tracking_exceeded"
    return "ok"


def expected_transitions(
    shell,
    a,
    b,
    *,
    start_s: float,
    duration_s: float,
    sample_s: float,
    max_range_km: float,
    max_rate_deg_s: float,
    resolution_s: float = 1.0,
) -> list[tuple[float, str]]:
    """Verdict changes at `resolution_s`, each reported at the first `sample_s` sample at or after it.

    The window opens `start_s` seconds after the epoch and times are reported
    from the window's opening. The first entry is the verdict at the opening.
    A transition the runtime samples at `sample_s` lands on that sample or the
    next one, so callers allow one sample.
    """
    out: list[tuple[float, str]] = []
    previous = None
    t = 0.0
    while t <= duration_s + 1e-9:
        v = verdict(
            *encounter(shell, a, b, start_s + t),
            max_range_km=max_range_km,
            max_rate_deg_s=max_rate_deg_s,
        )
        if v != previous:
            out.append((math.ceil(t / sample_s - 1e-9) * sample_s, v))
            previous = v
        t += resolution_s
    return out
