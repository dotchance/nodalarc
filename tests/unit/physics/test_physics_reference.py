"""The shared physics module against SPICE and against a numerical J2 reference.

Body centers within 1 m and 1 mm/s; body orientation within 1 millidegree;
the orbit-element basis within 1 millidegree of the same construction in
SPICE; a surface site on ICRF axes within 100 m. The production orbit kernel
within 1 cm and 1 mm/s of SPICE after 10 days for both orbit models,
including the singular cases, with each velocity within 1 mm/s of a
numerical derivative of the positions. The declared J2 rates within 0.1 %
of the rates of a numerically integrated J2 orbit.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import spiceypy
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import GeoPosition, Vec3
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import (
    ElementsBatch,
    OrbitModel,
    PhysicsContext,
    PhysicsDataError,
    body_fixed_from_geodetic,
    epoch_of,
    geodetic_from_body_fixed,
    orbit_states,
    secular_rates,
)
from nodalarc.physics.datasets import EphemerisLocation

EARTH = BodyFrame("earth", 6371.0088, 6378.137, 6356.752, 398600.4418, 1.08262668e-3)
LUNA = BodyFrame("luna", 1737.4, 1738.1, 1736.0, 4902.800066, 0.0)
SPICE_FRAME = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}
TEN_DAYS = [0.0, 1.0, 3600.0, 86400.0, 864000.0]


def _dates() -> list[float]:
    start = datetime(2026, 1, 15, tzinfo=UTC).timestamp()
    end = datetime(2030, 12, 15, tzinfo=UTC).timestamp()
    return list(np.linspace(start, end, 12))


def _et(master_sim_time_unix: float) -> float:
    return epoch_of(master_sim_time_unix).to_et_seconds()


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    r = np.asarray(a).T @ np.asarray(b)
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))))


@pytest.mark.usefixtures("spice_reference")
def test_moon_center_matches_spice(physics_context: PhysicsContext) -> None:
    for t in _dates():
        state = physics_context.body_state("luna", t)
        ref, _ = spiceypy.spkezr("MOON", _et(t), "J2000", "NONE", "EARTH")
        assert np.linalg.norm(np.array(state.position_km) - ref[:3]) < 1e-3
        assert np.linalg.norm(np.array(state.velocity_km_s) - ref[3:]) < 1e-6


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("body", ["earth", "luna"])
def test_orientation_and_rate_match_spice(physics_context: PhysicsContext, body: str) -> None:
    for t in _dates():
        orientation = physics_context.orientation(body, t)
        ref = np.array(spiceypy.sxform("J2000", SPICE_FRAME[body], _et(t)))
        assert _angle_deg(orientation.rotation, ref[:3, :3]) < 1e-3
        assert np.abs(orientation.rotation_rate - ref[3:, :3]).max() < 1e-10
        h = 1.0
        ahead = physics_context.orientation(body, t + h).rotation
        behind = physics_context.orientation(body, t - h).rotation
        assert np.abs((ahead - behind) / (2 * h) - orientation.rotation_rate).max() < 1e-10


@pytest.mark.usefixtures("spice_reference")
def test_lunar_orbit_basis_matches_spice_construction(physics_context: PhysicsContext) -> None:
    pole = np.array(spiceypy.pxform("J2000", "MOON_ME_DE440_ME421", 0.0))[2, :]
    x = np.cross([0.0, 0.0, 1.0], pole)
    x /= np.linalg.norm(x)
    reference = np.vstack([x, np.cross(pole, x), pole])
    assert _angle_deg(physics_context.orbit_basis("luna"), reference) < 1e-3
    np.testing.assert_array_equal(physics_context.orbit_basis("earth"), np.eye(3))


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize(
    ("body", "site"),
    [
        ("earth", GeoPosition(35.4267, -116.89, 1.0)),
        ("earth", GeoPosition(-35.4014, 148.9817, 0.69)),
        ("luna", GeoPosition(-89.45, -137.31, 1.2)),
        ("luna", GeoPosition(0.674, 23.473, 0.0)),
    ],
)
def test_site_position_on_icrf_axes_matches_spice(
    physics_context: PhysicsContext, body: str, site: GeoPosition
) -> None:
    frame = EARTH if body == "earth" else LUNA
    fixed = np.array(body_fixed_from_geodetic(site, frame))
    flattening = (frame.equatorial_radius_km - frame.polar_radius_km) / frame.equatorial_radius_km
    ref_fixed = np.array(
        spiceypy.georec(
            math.radians(site.lon_deg),
            math.radians(site.lat_deg),
            site.alt_km,
            frame.equatorial_radius_km,
            flattening,
        )
    )
    assert np.linalg.norm(fixed - ref_fixed) < 1e-6
    for t in _dates():
        icrf = physics_context.orientation(body, t).rotation.T @ fixed
        ref_icrf = np.array(spiceypy.pxform(SPICE_FRAME[body], "J2000", _et(t))) @ ref_fixed
        assert np.linalg.norm(icrf - ref_icrf) < 0.1


@pytest.mark.parametrize(
    ("body", "position"),
    [
        (EARTH, (0.0, 0.0, 6356.752 + 550.0)),
        (EARTH, (0.0, 0.0, -(6356.752 + 550.0))),
        (EARTH, (0.012, 0.0, 6356.752 + 550.0)),
        (EARTH, (4.2e-13, 0.0, 6356.752)),
        (EARTH, (6928.137, 0.0, 0.0)),
        (EARTH, (-2500.0, 4000.0, -4800.0)),
        (LUNA, (0.0, 0.0, -1836.0)),
        (LUNA, (900.0, -1200.0, 600.0)),
    ],
)
def test_body_fixed_to_geodetic_matches_spice_recgeo(body: BodyFrame, position) -> None:
    flattening = (body.equatorial_radius_km - body.polar_radius_km) / body.equatorial_radius_km
    ref_lon, ref_lat, ref_alt = spiceypy.recgeo(
        list(position), body.equatorial_radius_km, flattening
    )
    geo = geodetic_from_body_fixed(Vec3(*position), body)
    assert geo.lat_deg == pytest.approx(math.degrees(ref_lat), abs=1e-9)
    assert geo.alt_km == pytest.approx(ref_alt, abs=1e-6)
    if math.hypot(position[0], position[1]) > 1e-6:
        assert geo.lon_deg == pytest.approx(math.degrees(ref_lon), abs=1e-9)


def test_geodetic_round_trip_and_longitude_range() -> None:
    for lat, lon, alt in [(45.0, -170.0, 0.4), (-89.9, 179.5, 2.0), (0.0, 0.0, 550.0)]:
        geo = geodetic_from_body_fixed(
            body_fixed_from_geodetic(GeoPosition(lat, lon, alt), EARTH), EARTH
        )
        assert geo.lat_deg == pytest.approx(lat, abs=1e-9)
        assert geo.lon_deg == pytest.approx(lon, abs=1e-9)
        assert geo.alt_km == pytest.approx(alt, abs=1e-9)


ORBIT_CASES = {
    "exactly-circular": (
        EARTH,
        OrbitalElements(6928.137, math.radians(53), math.radians(40), mean_anomaly_rad=0.0),
    ),
    "equatorial": (
        EARTH,
        OrbitalElements(7000.0, 0.0, 0.0, eccentricity=0.001, mean_anomaly_rad=0.3),
    ),
    "at-periapsis-e0.58": (
        LUNA,
        OrbitalElements(
            (2 * 1737.4 + 673 + 7332) / 2,
            math.radians(46.8),
            math.radians(40),
            eccentricity=(7332 - 673) / (2 * 1737.4 + 673 + 7332),
            argument_of_perigee_rad=math.radians(20),
            mean_anomaly_rad=0.0,
        ),
    ),
    "at-apoapsis-e0.3": (
        EARTH,
        OrbitalElements(
            26600.0,
            math.radians(63.4),
            math.radians(200),
            eccentricity=0.3,
            argument_of_perigee_rad=math.radians(270),
            mean_anomaly_rad=math.pi,
        ),
    ),
    "retrograde-98deg-e0.01": (
        EARTH,
        OrbitalElements(
            7078.137,
            math.radians(98.2),
            math.radians(120),
            eccentricity=0.01,
            argument_of_perigee_rad=math.radians(90),
            mean_anomaly_rad=0.5,
        ),
    ),
    "retrograde-93deg-lunar": (
        LUNA,
        OrbitalElements(
            1837.4, math.radians(93), math.radians(30), eccentricity=1e-4, mean_anomaly_rad=0.0
        ),
    ),
}


def _kernel(el: OrbitalElements, times: list[float], model: OrbitModel, body: BodyFrame):
    states = orbit_states(
        ElementsBatch.from_elements([el]), np.array(times), model=model, body_frame=body
    )
    position = np.stack([states.px[0], states.py[0], states.pz[0]], axis=1)
    velocity = np.stack([states.vx[0], states.vy[0], states.vz[0]], axis=1)
    return position, velocity


def _spice_conics(el: OrbitalElements, mu: float, *, raan, argp, mean_anomaly) -> np.ndarray:
    perifocal = el.semi_major_axis_km * (1.0 - el.eccentricity)
    return np.array(
        spiceypy.conics(
            [perifocal, el.eccentricity, el.inclination_rad, raan, argp, mean_anomaly, 0.0, mu],
            0.0,
        )
    )


def _assert_velocity_is_position_derivative(
    el: OrbitalElements, model: OrbitModel, body: BodyFrame
) -> None:
    h = 0.5
    position, velocity = _kernel(el, [86400.0 - h, 86400.0, 86400.0 + h], model, body)
    assert np.linalg.norm((position[2] - position[0]) / (2 * h) - velocity[1]) < 1e-6


@pytest.mark.parametrize("case", sorted(ORBIT_CASES))
def test_two_body_kernel_matches_spice_prop2b(case: str) -> None:
    body, el = ORBIT_CASES[case]
    mu = body.gravitational_parameter_km3_s2
    start = _spice_conics(
        el, mu, raan=el.raan_rad, argp=el.argument_of_perigee_rad, mean_anomaly=el.mean_anomaly_rad
    )
    position, velocity = _kernel(el, TEN_DAYS, "two-body", body)
    for index, t in enumerate(TEN_DAYS):
        ref = np.array(spiceypy.prop2b(mu, start, t))
        assert np.linalg.norm(position[index] - ref[:3]) < 1e-5, (case, t)
        assert np.linalg.norm(velocity[index] - ref[3:]) < 1e-6, (case, t)
    _assert_velocity_is_position_derivative(el, "two-body", body)


@pytest.mark.parametrize("case", sorted(ORBIT_CASES))
def test_j2_kernel_matches_spice_conics_under_the_declared_rates(case: str) -> None:
    body, el = ORBIT_CASES[case]
    mu = body.gravitational_parameter_km3_s2
    rates = secular_rates(
        ElementsBatch.from_elements([el]), model="j2-mean-elements", body_frame=body
    )
    raan_dot = float(rates.raan_dot[0])
    argp_dot = float(rates.argument_of_perigee_dot[0])
    mean_anomaly_dot = float(rates.mean_anomaly_dot[0])
    mean_motion = math.sqrt(mu / el.semi_major_axis_km**3)
    position, velocity = _kernel(el, TEN_DAYS, "j2-mean-elements", body)
    for index, t in enumerate(TEN_DAYS):
        ref = _spice_conics(
            el,
            mu,
            raan=el.raan_rad + raan_dot * t,
            argp=el.argument_of_perigee_rad + argp_dot * t,
            mean_anomaly=el.mean_anomaly_rad + mean_anomaly_dot * t,
        )
        r = ref[:3]
        normal = np.cross(r, ref[3:])
        normal /= np.linalg.norm(normal)
        ref_velocity = (
            (mean_anomaly_dot / mean_motion) * ref[3:]
            + raan_dot * np.cross([0.0, 0.0, 1.0], r)
            + argp_dot * np.cross(normal, r)
        )
        assert np.linalg.norm(position[index] - r) < 1e-5, (case, t)
        assert np.linalg.norm(velocity[index] - ref_velocity) < 1e-6, (case, t)
    _assert_velocity_is_position_derivative(el, "j2-mean-elements", body)


def _j2_acceleration(r: np.ndarray, body: BodyFrame) -> np.ndarray:
    mu = body.gravitational_parameter_km3_s2
    re = body.equatorial_radius_km
    x, y, z = r
    r2 = x * x + y * y + z * z
    rn = math.sqrt(r2)
    k = 1.5 * body.j2 * mu * re * re / (rn**5)
    zz = 5.0 * z * z / r2
    return -mu * r / rn**3 + k * np.array([x * (zz - 1.0), y * (zz - 1.0), z * (zz - 3.0)])


def _osculating(
    r: np.ndarray, v: np.ndarray, mu: float
) -> tuple[float, float, float, float, float]:
    h = np.cross(r, v)
    node = np.cross([0.0, 0.0, 1.0], h)
    e_vec = np.cross(v, h) / mu - r / np.linalg.norm(r)
    a = 1.0 / (2.0 / np.linalg.norm(r) - (v @ v) / mu)
    inc = math.acos(h[2] / np.linalg.norm(h))
    raan = math.atan2(node[1], node[0])
    argp = math.atan2((np.cross(node, e_vec) @ h) / np.linalg.norm(h), node @ e_vec)
    return a, float(np.linalg.norm(e_vec)), inc, raan, argp


def test_declared_j2_rates_match_a_numerically_integrated_j2_orbit() -> None:
    """Accuracy of the declared approximation, separate from implementation agreement.

    A fourth-order Runge-Kutta integration of two-body plus J2 acceleration
    runs three days from the kernel's state. Straight-line fits to the
    osculating node and argument of perigee give the numerical secular
    rates, and the declared rates are evaluated at the run's mean elements.
    """
    el = OrbitalElements(
        7000.0,
        math.radians(53.0),
        math.radians(40.0),
        eccentricity=0.01,
        argument_of_perigee_rad=math.radians(30.0),
        mean_anomaly_rad=0.0,
    )
    mu = EARTH.gravitational_parameter_km3_s2
    position, velocity = _kernel(el, [0.0], "j2-mean-elements", EARTH)
    state = np.concatenate([position[0], velocity[0]])
    step = 20.0
    steps = int(3 * 86400 / step)

    def derivative(s: np.ndarray) -> np.ndarray:
        return np.concatenate([s[3:], _j2_acceleration(s[:3], EARTH)])

    samples = []
    for index in range(steps + 1):
        if index % 15 == 0:
            samples.append((index * step, *_osculating(state[:3], state[3:], mu)))
        k1 = derivative(state)
        k2 = derivative(state + 0.5 * step * k1)
        k3 = derivative(state + 0.5 * step * k2)
        k4 = derivative(state + step * k3)
        state = state + step / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
    table = np.array(samples)
    t = table[:, 0]
    raan_fit = np.polyfit(t, np.unwrap(table[:, 4]), 1)[0]
    argp_fit = np.polyfit(t, np.unwrap(table[:, 5]), 1)[0]
    mean = OrbitalElements(
        float(table[:, 1].mean()),
        float(table[:, 3].mean()),
        el.raan_rad,
        eccentricity=float(table[:, 2].mean()),
        argument_of_perigee_rad=el.argument_of_perigee_rad,
        mean_anomaly_rad=0.0,
    )
    declared = secular_rates(
        ElementsBatch.from_elements([mean]), model="j2-mean-elements", body_frame=EARTH
    )
    assert float(declared.raan_dot[0]) == pytest.approx(raan_fit, rel=1e-3)
    assert float(declared.argument_of_perigee_dot[0]) == pytest.approx(argp_fit, rel=1e-3)


def test_time_outside_coverage_is_refused(physics_context: PhysicsContext) -> None:
    after_coverage = datetime(2160, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.body_state("luna", after_coverage)
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.orientation("earth", datetime(1999, 1, 1, tzinfo=UTC).timestamp())


def test_session_without_earth_loads_no_earth_orientation(
    physics_location: EphemerisLocation,
) -> None:
    luna_only = PhysicsContext(["luna"], location=physics_location)
    assert [f.file_id for f in luna_only.selected_files] == [
        "de440s",
        "moon-orientation",
        "moon-frames",
    ]
    assert luna_only.body_state("luna", 1.78e9).position_km[0] != 0.0
    with pytest.raises(PhysicsDataError, match="not in this session"):
        luna_only.orientation("earth", 1.78e9)


def test_file_that_does_not_match_its_pin_is_refused(
    tmp_path: Path, physics_location: EphemerisLocation
) -> None:
    for item in physics_location.source_dir.iterdir():
        if item.is_file():
            (tmp_path / item.name).symlink_to(item)
    (tmp_path / "earth_latest_high_prec.bpc").unlink()
    (tmp_path / "earth_latest_high_prec.bpc").write_bytes(b"not the pinned file")
    location = EphemerisLocation(source_dir=tmp_path, converted_dir=physics_location.converted_dir)
    with pytest.raises(PhysicsDataError, match="does not match its pin"):
        PhysicsContext(["earth"], location=location)


def test_unconverted_datasets_are_refused(
    tmp_path: Path, physics_location: EphemerisLocation
) -> None:
    location = EphemerisLocation(source_dir=physics_location.source_dir, converted_dir=tmp_path)
    with pytest.raises(PhysicsDataError, match="converted ephemeris datasets are missing"):
        PhysicsContext(["luna"], location=location)


def _mars_dates() -> list[float]:
    start = datetime(2026, 1, 15, tzinfo=UTC).timestamp()
    end = datetime(2049, 6, 15, tzinfo=UTC).timestamp()
    return list(np.linspace(start, end, 12))


@pytest.mark.usefixtures("spice_reference")
def test_mars_center_and_orientation_match_spice(table_context: PhysicsContext) -> None:
    """Mars's center (from the Mars satellite ephemeris) within 1 m and 1 mm/s; IAU_MARS from
    the planetary constants within 1 millidegree, with its rate."""
    for t in _mars_dates():
        state = table_context.body_state("mars", t)
        ref, _ = spiceypy.spkezr("MARS", _et(t), "J2000", "NONE", "EARTH")
        assert np.linalg.norm(np.array(state.position_km) - ref[:3]) < 1e-3
        assert np.linalg.norm(np.array(state.velocity_km_s) - ref[3:]) < 1e-6
        orientation = table_context.orientation("mars", t)
        rotation = np.array(spiceypy.sxform("J2000", "IAU_MARS", _et(t)))
        assert _angle_deg(orientation.rotation, rotation[:3, :3]) < 1e-3
        assert np.abs(orientation.rotation_rate - rotation[3:, :3]).max() < 1e-10


def test_mars_data_covers_1995_to_2050(physics_location: EphemerisLocation) -> None:
    """A Mars session loads no Earth orientation data, so the Mars satellite ephemeris alone
    bounds its dates. The file's limits are TDB midnights, 69 s before the UTC midnights."""
    context = PhysicsContext(["mars"], location=physics_location)
    start = datetime(1995, 1, 1, tzinfo=UTC).timestamp()
    end = datetime(2050, 1, 1, tzinfo=UTC).timestamp()
    assert context.coverage_gap(start + 3600.0, end - 3600.0) is None
    gap = context.coverage_gap(start + 3600.0, end + 3600.0)
    assert gap is not None and gap.body_id == "mars"


def test_mars_files_leave_earth_and_luna_unchanged(
    physics_context: PhysicsContext, table_context: PhysicsContext
) -> None:
    """Mars's satellite ephemeris also carries Earth, the Earth-Moon barycenter and the Sun, and
    its planetary constants define IAU frames for Earth and the Moon. Loading them changes no
    Earth or Luna fact."""
    for t in _dates():
        assert table_context.body_state("luna", t) == physics_context.body_state("luna", t)
        assert table_context.sun_state(t) == physics_context.sun_state(t)
        for body in ("earth", "luna"):
            with_mars = table_context.orientation(body, t)
            without = physics_context.orientation(body, t)
            assert np.array_equal(with_mars.rotation, without.rotation)
            assert np.array_equal(with_mars.rotation_rate, without.rotation_rate)
