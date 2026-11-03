"""TLE states against published reference values.

Reference: D. A. Vallado, P. Crawford, R. Hujsak and T. S. Kelso, "Revisiting
Spacetrack Report #3", AIAA 2006-6753. Its verification output gives SGP4 TEME
states for test TLEs (WGS-72), and its Appendix C gives an SGP4 TEME state with
its IAU-76/FK5 J2000 conversion, and an ITRF state with its TEME conversion.

The TLE path runs SGP4 and Skyfield's TEME-to-GCRS rotation, then the shared
physics module's ITRF93 orientation for the Earth-fixed state. Each test below
checks one step against the published values.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from nodalarc.physics import PhysicsContext
from nodalarc.physics.satellites import states_from_icrf
from nodalarc.propagator import _skyfield_satellite, propagate_sgp4_tle_gcrs

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME

# Near-Earth test TLE (Vanguard 1 rocket body).
TLE_00005 = (
    "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
    "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
)
# Deep-space test TLE (12-hour orbit, SDP4 branch), propagated backwards.
TLE_04632 = (
    "1 04632U 70093B   04031.91070959 -.00000084  00000-0  10000-3 0  9955",
    "2 04632  11.4628 273.1101 1450506 207.6000 143.9350  1.20231981 44145",
)
# Sub-orbital test TLE: perigee -51 km, decays about 50 minutes after epoch.
TLE_28872 = (
    "1 28872U 05037B   05333.02012661  .25992681  00000-0  24476-3 0  1534",
    "2 28872  96.4736 157.9986 0303955 244.0492 110.6523 16.46015938 10708",
)

# Minutes from epoch, TEME position (km), TEME velocity (km/s), as published.
PUBLISHED_TEME = {
    "00005": (
        TLE_00005,
        (
            (
                0.0,
                (7022.46529266, -1400.08296755, 0.03995155),
                (1.893841015, 6.405893759, 4.534807250),
            ),
            (
                360.0,
                (-7154.03120202, -3783.17682504, -3536.19412294),
                (4.741887409, -4.151817765, -2.093935425),
            ),
            (
                720.0,
                (-7134.59340119, 6531.68641334, 3260.27186483),
                (-4.113793027, -2.911922039, -2.557327851),
            ),
            (
                1080.0,
                (5568.53901181, 4492.06992591, 3863.87641983),
                (-4.209106476, 5.159719888, 2.744852980),
            ),
            (
                1440.0,
                (-938.55923943, -6268.18748831, -4294.02924751),
                (7.536105209, -0.427127707, 0.989878080),
            ),
            (
                1800.0,
                (-9680.56121728, 2802.47771354, 124.10688038),
                (-0.905874102, -4.659467970, -3.227347517),
            ),
            (
                2160.0,
                (190.19796988, 7746.96653614, 5110.00675412),
                (-6.112325142, 1.527008184, -0.139152358),
            ),
            (
                2520.0,
                (5579.55640116, -3995.61396789, -1518.82108966),
                (4.767927483, 5.123185301, 4.276837355),
            ),
            (
                2880.0,
                (-8650.73082219, -1914.93811525, -3007.03603443),
                (3.067165127, -4.828384068, -2.515322836),
            ),
            (
                3240.0,
                (-5429.79204164, 7574.36493792, 3747.39305236),
                (-4.999442110, -1.800561422, -2.229392830),
            ),
            (
                3600.0,
                (6759.04583722, 2001.58198220, 2783.55192533),
                (-2.180993947, 6.402085603, 3.644723952),
            ),
            (
                3960.0,
                (-3791.44531559, -5712.95617894, -4533.48630714),
                (6.668817493, -2.516382327, -0.082384354),
            ),
            (
                4320.0,
                (-9060.47373569, 4658.70952502, 813.68673153),
                (-2.232832783, -4.110453490, -3.157345433),
            ),
        ),
    ),
    "04632": (
        TLE_04632,
        (
            (
                0.0,
                (2334.11450085, -41920.44035349, -0.03867437),
                (2.826321032, -0.065091664, 0.570936053),
            ),
            (
                -5184.0,
                (-29020.02587128, 13819.84419063, -5713.33679183),
                (-1.768068390, -3.235371192, -0.395206135),
            ),
            (
                -5064.0,
                (-32982.56870101, -11125.54996609, -6803.28472771),
                (0.617446996, -3.379240041, 0.085954707),
            ),
            (
                -4944.0,
                (-22097.68730513, -31583.13829284, -4836.34329328),
                (2.230597499, -2.166594667, 0.426443070),
            ),
            (
                -4896.0,
                (-15129.94694545, -36907.74526221, -3487.56256701),
                (2.581167187, -1.524204737, 0.504805763),
            ),
        ),
    ),
}

# Published values carry 1e-8 km and 1e-9 km/s per axis, so rounding alone
# allows 8.7e-9 km and 0.87e-9 km/s on the vector. Measured worst cases:
# 6.8e-9 km and 0.77e-9 km/s.
TEME_POSITION_TOL_KM = 1.0e-8
TEME_VELOCITY_TOL_KM_S = 1.0e-9


def _distance(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


@pytest.mark.parametrize("satnum", sorted(PUBLISHED_TEME))
def test_sgp4_matches_published_teme_states(satnum: str) -> None:
    tle, rows = PUBLISHED_TEME[satnum]
    satellite, _timescale = _skyfield_satellite(*tle)
    for minutes, position, velocity in rows:
        error, r_teme, v_teme = satellite.model.sgp4_tsince(minutes)
        assert error == 0, f"{satnum} at {minutes} min: SGP4 error {error}"
        assert _distance(r_teme, position) < TEME_POSITION_TOL_KM, (satnum, minutes)
        assert _distance(v_teme, velocity) < TEME_VELOCITY_TOL_KM_S, (satnum, minutes)


def test_gcrs_state_matches_published_j2000_of_date() -> None:
    """TLE 00005 three days after epoch, TEME to inertial axes.

    The published vector is IAU-76/FK5 J2000 with the rotation evaluated at
    the propagated instant ("of date"). Skyfield's GCRS uses the IAU 2006/2000A
    models. Measured: 0.134 m and 0.068 mm/s. The published "of epoch" vector
    lies 23.6 m away; ANISE's TEME frames land 0.54 m to 1.57 m away.
    """
    epoch_unix = datetime(2000, 6, 27, tzinfo=UTC).timestamp() + 0.78495062 * 86400.0
    position, velocity = propagate_sgp4_tle_gcrs(*TLE_00005, epoch_unix + 3 * 86400.0)

    assert _distance(position, (-9059.9415541, 4659.6971990, 813.9569402)) < 0.2e-3
    assert _distance(velocity, (-2.233347413, -4.110136158, -3.157394560)) < 0.1e-6


def test_earth_fixed_state_matches_published_itrf(physics_context: PhysicsContext) -> None:
    """The published TEME state of 2004-04-06 07:51:28.386 UTC, to ITRF.

    Skyfield's TEME-to-GCRS rotation (the rotation ``propagate_sgp4_tle_gcrs``
    applies) gives the inertial state; the shared physics module gives the
    Earth-fixed state from NAIF's ITRF93 orientation. The published conversion
    uses IAU-76/FK5 with the paper's Earth orientation values; NAIF's file
    carries its own series. Measured: 0.434 m and 0.154 mm/s. Skyfield's own
    Earth-fixed frame, which NodalArc used before, is 16.3 m away.
    """
    from skyfield.sgp4lib import TEME

    unix = datetime(2004, 4, 6, 7, 51, 28, 386000, tzinfo=UTC).timestamp()
    _satellite, timescale = _skyfield_satellite(*TLE_00005)
    t = timescale.from_datetime(datetime.fromtimestamp(unix, UTC))
    teme_to_gcrs = np.asarray(TEME.rotation_at(t)).T
    r_teme = np.array([5094.18010720, 6127.64470520, 6380.34453270])
    v_teme = np.array([-4.746131494, 0.785817998, 5.531931288])

    states = states_from_icrf(
        physics_context,
        body_frame=EARTH_TEST_BODY_FRAME,
        position_icrf=(teme_to_gcrs @ r_teme)[None, :],
        velocity_icrf=(teme_to_gcrs @ v_teme)[None, :],
        master_sim_time_unix=unix,
    )

    assert (
        _distance(states.position_fixed_km[0], (-1033.47938300, 7901.29527540, 6380.35659580))
        < 0.5e-3
    )
    assert (
        _distance(states.velocity_fixed_km_s[0], (-3.225636520, -2.872451450, 5.531924446)) < 0.3e-6
    )


def test_sgp4_error_refuses_the_state() -> None:
    """A decayed satellite has no position; NodalArc does not invent one."""
    satellite, _timescale = _skyfield_satellite(*TLE_28872)
    epoch_unix = satellite.epoch.utc_datetime().timestamp()

    propagate_sgp4_tle_gcrs(*TLE_28872, epoch_unix + 50 * 60.0)
    with pytest.raises(ValueError, match="SGP4 refused TLE 28872 .* decayed"):
        propagate_sgp4_tle_gcrs(*TLE_28872, epoch_unix + 60 * 60.0)
