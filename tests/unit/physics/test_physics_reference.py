"""The shared physics module against SPICE.

Body centers within 1 m and 1 mm/s; body orientation within 1 millidegree;
the orbit-element basis within 1 millidegree of the same construction in
SPICE; a surface site on ICRF axes within 100 m; the analytic orbit models
within 1 cm and 1 mm/s of their references after 10 days, including the
singular cases, and their velocities within 1 mm/s of a numerical
derivative of their positions.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import spiceypy
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import (
    SPICE_LOCK,
    PhysicsContext,
    PhysicsDataError,
    body_fixed_from_geodetic,
    epoch_of,
    geodetic_from_body_fixed,
    j2_mean_states,
    two_body_states,
)
from nodalarc.physics.datasets import EphemerisLocation

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "physics"
EARTH = BodyFrame("earth", 6371.0088, 6378.137, 6356.752, 7.2921159e-5, 398600.4418, 1.08262668e-3)
LUNA = BodyFrame("luna", 1737.4, 1738.1, 1736.0, 2.6616995e-6, 4902.800066, 0.0)
SPICE_FRAME = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}


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
        with SPICE_LOCK:
            ref, _ = spiceypy.spkezr("MOON", _et(t), "J2000", "NONE", "EARTH")
        assert np.linalg.norm(np.array(state.position_km) - ref[:3]) < 1e-3
        assert np.linalg.norm(np.array(state.velocity_km_s) - ref[3:]) < 1e-6


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("body", ["earth", "luna"])
def test_orientation_and_rate_match_spice(physics_context: PhysicsContext, body: str) -> None:
    for t in _dates():
        orientation = physics_context.orientation(body, t)
        with SPICE_LOCK:
            ref = np.array(spiceypy.sxform("J2000", SPICE_FRAME[body], _et(t)))
        assert _angle_deg(orientation.rotation, ref[:3, :3]) < 1e-3
        assert np.abs(orientation.rotation_rate - ref[3:, :3]).max() < 1e-10
        h = 1.0
        ahead = physics_context.orientation(body, t + h).rotation
        behind = physics_context.orientation(body, t - h).rotation
        assert np.abs((ahead - behind) / (2 * h) - orientation.rotation_rate).max() < 1e-10


@pytest.mark.usefixtures("spice_reference")
def test_lunar_orbit_basis_matches_spice_construction(physics_context: PhysicsContext) -> None:
    with SPICE_LOCK:
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
    with SPICE_LOCK:
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
        with SPICE_LOCK:
            ref_icrf = np.array(spiceypy.pxform(SPICE_FRAME[body], "J2000", _et(t))) @ ref_fixed
        assert np.linalg.norm(icrf - ref_icrf) < 0.1


def test_geodetic_round_trip_and_longitude_range() -> None:
    for lat, lon, alt in [(45.0, -170.0, 0.4), (-89.9, 179.5, 2.0), (0.0, 0.0, 550.0)]:
        geo = geodetic_from_body_fixed(
            body_fixed_from_geodetic(GeoPosition(lat, lon, alt), EARTH), EARTH
        )
        assert geo.lat_deg == pytest.approx(lat, abs=1e-9)
        assert geo.lon_deg == pytest.approx(lon, abs=1e-9)
        assert geo.alt_km == pytest.approx(alt, abs=1e-9)


TWO_BODY_CASES = {
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
    "at-apoapsis": (
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
    "retrograde-93deg": (
        LUNA,
        OrbitalElements(
            1837.4, math.radians(93), math.radians(30), eccentricity=1e-4, mean_anomaly_rad=0.0
        ),
    ),
}


def _elements_state(el: OrbitalElements, mu: float) -> np.ndarray:
    rp = el.semi_major_axis_km * (1.0 - el.eccentricity)
    with SPICE_LOCK:
        return np.array(
            spiceypy.conics(
                [
                    rp,
                    el.eccentricity,
                    el.inclination_rad,
                    el.raan_rad,
                    el.argument_of_perigee_rad,
                    el.mean_anomaly_rad,
                    0.0,
                    mu,
                ],
                0.0,
            )
        )


@pytest.mark.parametrize("case", sorted(TWO_BODY_CASES))
def test_two_body_state_contract(case: str) -> None:
    body, el = TWO_BODY_CASES[case]
    mu = body.gravitational_parameter_km3_s2
    start = _elements_state(el, mu)
    times = [1.0, 3600.0, 86400.0, 864000.0]
    states = two_body_states([el] * len(times), times, mu_km3_s2=mu)
    for index, t in enumerate(times):
        with SPICE_LOCK:
            ref = np.array(spiceypy.prop2b(mu, start, t))
        assert np.linalg.norm(states.position_km[index] - ref[:3]) < 1e-5
        assert np.linalg.norm(states.velocity_km_s[index] - ref[3:]) < 1e-6
    h = 0.5
    ahead = two_body_states([el], [86400.0 + h], mu_km3_s2=mu).position_km[0]
    behind = two_body_states([el], [86400.0 - h], mu_km3_s2=mu).position_km[0]
    assert np.linalg.norm((ahead - behind) / (2 * h) - states.velocity_km_s[2]) < 1e-6


def test_j2_states_match_previous_model_and_their_own_derivative() -> None:
    reference = json.loads((FIXTURE / "j2_mean_elements_reference.json").read_text())
    body = reference["body"]
    frame = BodyFrame(
        "earth",
        6371.0088,
        body["equatorial_radius_km"],
        body["polar_radius_km"],
        7.2921159e-5,
        body["gravitational_parameter_km3_s2"],
        body["j2"],
    )
    for name, case in reference["cases"].items():
        c = case["elements_deg"]
        el = OrbitalElements(
            c["a"],
            math.radians(c["i"]),
            math.radians(c["raan"]),
            eccentricity=c["e"],
            argument_of_perigee_rad=math.radians(c["argp"]),
            mean_anomaly_rad=math.radians(c["m"]),
        )
        times = [s["dt_s"] for s in case["samples"]]
        states = j2_mean_states([el] * len(times), times, body_frame=frame)
        for index, sample in enumerate(case["samples"]):
            assert np.linalg.norm(states.position_km[index] - sample["position_km"]) < 1e-5, name
            assert np.linalg.norm(states.velocity_km_s[index] - sample["velocity_km_s"]) < 1e-6, (
                name
            )
        h = 0.5
        ahead = j2_mean_states([el], [86400.0 + h], body_frame=frame).position_km[0]
        behind = j2_mean_states([el], [86400.0 - h], body_frame=frame).position_km[0]
        at = j2_mean_states([el], [86400.0], body_frame=frame).velocity_km_s[0]
        assert np.linalg.norm((ahead - behind) / (2 * h) - at) < 1e-6, name


def test_time_outside_coverage_is_refused(physics_context: PhysicsContext) -> None:
    after_coverage = datetime(2160, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.body_state("luna", after_coverage)
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.orientation("earth", datetime(1999, 1, 1, tzinfo=UTC).timestamp())


def test_body_outside_the_context_is_refused(physics_location: EphemerisLocation) -> None:
    earth_only = PhysicsContext(["earth"], location=physics_location)
    with pytest.raises(PhysicsDataError, match="not in this session"):
        earth_only.body_state("luna", 1.78e9)


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


def test_selected_files_name_every_loaded_file(physics_context: PhysicsContext) -> None:
    assert [f.file_id for f in physics_context.selected_files] == [
        "de440s",
        "earth-orientation-predict",
        "earth-orientation-measured",
        "moon-orientation",
        "moon-frames",
    ]
    assert all(len(f.sha256) == 64 for f in physics_context.selected_files)
