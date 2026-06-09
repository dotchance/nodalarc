"""SGP4 reference fixtures for high-fidelity propagation gates.

Raw SGP4 produces TEME state vectors. NodalArc's physics contract needs ECEF
state for range, visibility, and latency, so this file also locks down the
Skyfield TEME-to-ITRS conversion used by the production SGP4 engine.
"""

from __future__ import annotations

import pytest
from nodalarc.propagator import propagate_sgp4_tle
from sgp4.api import Satrec

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"

VANGUARD_1_TLE_LINE_1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
VANGUARD_1_TLE_LINE_2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"

NOAA_14_TLE_LINE_1 = "1 23455U 94089A   97320.90946019  .00000140  00000-0  10191-3 0  2621"
NOAA_14_TLE_LINE_2 = "2 23455  99.0090 272.6745 0008546 223.1686 136.8816 14.11711747148495"


REFERENCE_CASES = (
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        0.0,
        (-4251.971748597022, 2357.358738349331, 4740.4057076426725),
        (-5.557632235794692, -4.5159463531633115, -2.7313550530977366),
    ),
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        3600.0,
        (6508.565036110713, 1707.402506514798, -981.9621644319247),
        (-0.39186198852796383, 4.860355573318967, 5.905417820753693),
    ),
    (
        "iss",
        ISS_TLE_LINE_1,
        ISS_TLE_LINE_2,
        21600.0,
        (580.1044859144467, 4488.375601723018, 5061.453426970322),
        (-7.3458865911793065, -1.1516813754037765, 1.8576788694520805),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        0.0,
        (7022.465297429343, -1400.0829514347054, 0.03996296163552402),
        (1.8938409953147717, 6.4058937630381365, 4.534807250354833),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        3600.0,
        (-8198.270000869177, 5546.904772481435, 2599.0678906489925),
        (-3.294076397325598, -3.582921896037862, -2.838098690266733),
    ),
    (
        "vanguard-1",
        VANGUARD_1_TLE_LINE_1,
        VANGUARD_1_TLE_LINE_2,
        21600.0,
        (-7154.031190084393, -3783.176835483181, -3536.194128211291),
        (4.741887419413347, -4.151817759863794, -2.0939354197478646),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        0.0,
        (337.78759680613814, -7231.179777460484, 0.004997558931635687),
        (-1.1600236659585037, -0.05093330949129727, 7.328315300243385),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        3600.0,
        (304.6530892237869, 6169.066489956457, -3761.0190253226187),
        (1.172080157481795, -3.8565456711772836, -6.235575318250629),
    ),
    (
        "noaa-14",
        NOAA_14_TLE_LINE_1,
        NOAA_14_TLE_LINE_2,
        21600.0,
        (-167.93951748295362, 7122.45577244262, -1234.100211502585),
        (1.2089280892420655, -1.2206106644705454, -7.226829478325151),
    ),
)


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
    ("name", "tle_line_1", "tle_line_2", "offset_s", "expected_r_km", "expected_v_km_s"),
    REFERENCE_CASES,
)
def test_tle_sgp4_reference_positions(
    name,
    tle_line_1,
    tle_line_2,
    offset_s,
    expected_r_km,
    expected_v_km_s,
):
    del name
    sat = Satrec.twoline2rv(tle_line_1, tle_line_2)
    jd = sat.jdsatepoch + sat.jdsatepochF + offset_s / 86400.0

    error_code, position_km, velocity_km_s = sat.sgp4(jd, 0.0)

    assert error_code == 0
    assert position_km == pytest.approx(expected_r_km, abs=1e-9)
    assert velocity_km_s == pytest.approx(expected_v_km_s, abs=1e-12)


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
