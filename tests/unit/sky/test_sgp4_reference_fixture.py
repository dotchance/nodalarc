"""SGP4 reference fixtures for high-fidelity propagation gates.

Raw SGP4 produces TEME state vectors. NodalArc's physics contract needs ECEF
state for range, visibility, and latency, so this file also locks down the
Skyfield TEME-to-ITRS conversion used by the production SGP4 engine.
"""

from __future__ import annotations

import pytest
from nodalarc.propagator import propagate_sgp4_tle

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"

VANGUARD_1_TLE_LINE_1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
VANGUARD_1_TLE_LINE_2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"

NOAA_14_TLE_LINE_1 = "1 23455U 94089A   97320.90946019  .00000140  00000-0  10191-3 0  2621"
NOAA_14_TLE_LINE_2 = "2 23455  99.0090 272.6745 0008546 223.1686 136.8816 14.11711747148495"


ECEF_REFERENCE_CASES = (
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        1615896900.000275,
        0.0,
        (-4329.375350762542, 2211.9930425759426, 4740.40568912658),
        (-5.240188571438462, -4.385887860221932, -2.731355094043767),
    ),
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        1615896900.000275,
        3600.0,
        (6726.171294751593, 187.78203935690095, -981.9620452008595),
        (0.7336437084563668, 4.332204086584382, 5.905417845990117),
    ),
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        1615896900.000275,
        21600.0,
        (4503.567107768781, -447.1240707925361, 5061.453439563637),
        (-1.401009271628959, 6.980212166897634, 1.85767882574363),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        962131819.733582,
        0.0,
        (-6198.504138300809, 3585.2193337967483, 0.04001502728467891),
        (-3.5928884490749566, -5.0038456102662305, 4.5348072503552315),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        962131819.733582,
        3600.0,
        (3725.1784128209065, -9170.759494716629, 2599.0678199654835),
        (4.061964592544734, 0.8723314565357861, -2.8380987143897856),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        962131819.733582,
        21600.0,
        (1245.6782647451337, -7996.303801105612, -3536.194152260217),
        (4.8872140355376885, 3.0394600564140526, -2.093935396199013),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        879716977.360443,
        0.0,
        (-2562.8735272577637, -6770.209798625656, 0.0050574299144116154),
        (-1.5784735017855795, 0.60100974717257, 7.328315300243319),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        879716977.360443,
        3600.0,
        (4074.692746924767, 4641.882470396415, -3761.0191598919027),
        (-1.1417011336371226, -4.046230841564638, -6.235575232523012),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        879716977.360443,
        21600.0,
        (6591.388180452203, -2703.9196772755863, -1234.1002705445321),
        (-1.8002834345215102, -1.0982553550178316, -7.226829467655389),
    ),
)


@pytest.mark.parametrize(
    (
        "name",
        "tle_line_1",
        "tle_line_2",
        "epoch_unix",
        "offset_s",
        "expected_ecef_km",
        "expected_ecef_velocity_km_s",
    ),
    ECEF_REFERENCE_CASES,
)
def test_tle_sgp4_ecef_reference_positions(
    name,
    tle_line_1,
    tle_line_2,
    epoch_unix,
    offset_s,
    expected_ecef_km,
    expected_ecef_velocity_km_s,
):
    del name
    position, velocity, geo = propagate_sgp4_tle(
        tle_line_1,
        tle_line_2,
        epoch_unix,
        offset_s,
        body_frame=EARTH_TEST_BODY_FRAME,
    )

    assert (position.x, position.y, position.z) == pytest.approx(expected_ecef_km, abs=1e-6)
    assert (velocity.x, velocity.y, velocity.z) == pytest.approx(
        expected_ecef_velocity_km_s,
        abs=1e-9,
    )
    assert -90.0 <= geo.lat_deg <= 90.0
    assert -180.0 <= geo.lon_deg <= 180.0
