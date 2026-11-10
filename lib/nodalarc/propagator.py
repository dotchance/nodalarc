# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""SGP4/TLE propagation to Earth-centered ICRF axes through Skyfield.

This module produces one thing: a TLE satellite's state relative to Earth's
center on GCRS axes, which are ICRF axes. The Earth-fixed state comes from
the shared physics module (``nodalarc.physics.satellites``), which applies
NAIF's ITRF93 orientation, so TLE satellites, analytic satellites and Earth
sites share one Earth orientation.

Why Skyfield for TEME to GCRS. SGP4 states are in TEME. Published reference:
Vallado et al. 2006, "Revisiting Spacetrack Report #3", Appendix C, gives an
SGP4 TEME state and its IAU-76/FK5 J2000 conversion for TLE 00005, and an ITRF
state with its TEME conversion. Measured against those values:

- Skyfield's TEME-to-GCRS rotation: 0.13 m and 0.07 mm/s from the published
  J2000 state.
- ANISE's TEME frames (the alternative): ``EARTH_TEME`` lands 1.34 m away
  through ``J2000`` and 0.54 m through ``GCRF``; ``EARTH_TEME_LEGACY``
  (IAU-76/80) lands 0.82 m and 1.57 m away. They are not used.
- Skyfield's own Earth-fixed frame (the previous path): 16.3 m from the
  published ITRF state. It applies no polar motion; with the published polar
  motion applied to its output, 0.36 m remain. It is not used.
- Skyfield TEME to GCRS followed by ANISE's ITRF93 orientation: 0.43 m and
  0.15 mm/s from the published ITRF state.

The tests in ``tests/unit/physics/test_tle_reference.py`` hold these values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from nodalarc.frames import GcrsVec3


@lru_cache(maxsize=1)
def _skyfield_timescale():
    from skyfield.api import load

    return load.timescale()


@lru_cache(maxsize=4096)
def _skyfield_satellite(tle_line_1: str, tle_line_2: str):
    """Return a cached Skyfield EarthSatellite for a TLE pair."""
    from skyfield.api import EarthSatellite

    from nodalarc.tle import validate_tle_pair

    validate_tle_pair(tle_line_1, tle_line_2)
    timescale = _skyfield_timescale()
    return EarthSatellite(tle_line_1, tle_line_2, None, timescale), timescale


def propagate_sgp4_tle_gcrs(
    tle_line_1: str,
    tle_line_2: str,
    sim_time_unix: float,
) -> tuple[GcrsVec3, GcrsVec3]:
    """A TLE satellite's position (km) and velocity (km/s) at an instant.

    Relative to Earth's center on GCRS axes. Raises when SGP4 reports an
    error (for example a decayed orbit): SGP4 then returns no usable state,
    and NodalArc does not place the satellite anywhere.
    """
    sat, ts = _skyfield_satellite(tle_line_1, tle_line_2)
    t = ts.from_datetime(datetime.fromtimestamp(sim_time_unix, UTC))
    geocentric = sat.at(t)
    if geocentric.message is not None:
        raise ValueError(
            f"SGP4 refused TLE {sat.model.satnum} at unix time {sim_time_unix}: "
            f"{geocentric.message}"
        )
    position = GcrsVec3(*(float(v) for v in geocentric.position.km))
    velocity = GcrsVec3(*(float(v) for v in geocentric.velocity.km_per_s))
    return position, velocity
