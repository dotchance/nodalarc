"""Test constellation expansion from typed constellation models."""

from __future__ import annotations

import math

from nodalarc.constellation_loader import expand_constellation
from nodalarc.models.constellation import ConstellationConfig, ParametricConstellation
from pydantic import TypeAdapter

from tests.conftest import FIXTURES_DIR
from tests.physics_fixtures import EARTH_TEST_BODY_FRAME

adapter = TypeAdapter(ConstellationConfig)
EARTH_RADIUS_KM = EARTH_TEST_BODY_FRAME.mean_radius_km


def _expand(config: ConstellationConfig):
    return expand_constellation(config, body_frame=EARTH_TEST_BODY_FRAME)


def _terminal_config(isl_count: int, ground_count: int = 1) -> dict:
    return {
        "isl": [
            {
                "type": "optical",
                "count": isl_count,
                "max_range_km": 5000,
                "bandwidth_mbps": 1000,
                "max_tracking_rate_deg_s": 3.0,
            }
        ],
        "ground": [{"type": "rf", "count": ground_count, "bandwidth_mbps": 1000}],
    }


def _parametric(
    *,
    name: str = "starlink-early-44",
    planes: int = 4,
    slots: int = 11,
    raan_spacing_deg: float = 45.0,
    altitude_km: float = 550.0,
    inclination_deg: float = 53.0,
    pattern: str = "walker-delta",
    isl_count: int = 4,
) -> ParametricConstellation:
    return adapter.validate_python(
        {
            "mode": "parametric",
            "name": name,
            "orbit": {
                "altitude_km": altitude_km,
                "inclination_deg": inclination_deg,
                "pattern": pattern,
            },
            "planes": {
                "count": planes,
                "raan_spacing_deg": raan_spacing_deg,
                "sats_per_plane": slots,
                "phase_offset_deg": 0,
            },
            "default_terminals": _terminal_config(isl_count),
        }
    )


def _explicit() -> ConstellationConfig:
    return adapter.validate_python(
        {
            "mode": "explicit",
            "name": "custom-example",
            "default_terminals": _terminal_config(2),
            "satellites": [
                {
                    "plane": 0,
                    "slot": 0,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 0,
                    },
                },
                {
                    "plane": 0,
                    "slot": 1,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 180,
                    },
                },
                {
                    "plane": 1,
                    "slot": 0,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 45,
                        "true_anomaly_deg": 0,
                    },
                },
                {
                    "plane": 1,
                    "slot": 1,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 45,
                        "true_anomaly_deg": 180,
                    },
                },
            ],
        }
    )


class TestParametricExpansion:
    def test_starlink_early_count(self):
        sats = _expand(_parametric())
        assert len(sats) == 44

    def test_starlink_early_planes_and_slots(self):
        sats = _expand(_parametric())
        assert {s.plane for s in sats} == {0, 1, 2, 3}
        for plane in range(4):
            assert {s.slot for s in sats if s.plane == plane} == set(range(11))

    def test_starlink_early_altitude(self):
        sats = _expand(_parametric())
        for sat in sats:
            alt = sat.elements.semi_major_axis_km - EARTH_RADIUS_KM
            assert abs(alt - 550.0) < 0.01

    def test_starlink_early_raan_spacing(self):
        sats = _expand(_parametric())
        for plane in range(4):
            sat = next(s for s in sats if s.plane == plane and s.slot == 0)
            assert abs(sat.elements.raan_rad - math.radians(plane * 45.0)) < 1e-10

    def test_starlink_early_raan_spread(self):
        config = _parametric()
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert raan_spread == 180.0
        assert raan_spread < 360.0

    def test_iridium_66_count(self):
        sats = _expand(
            _parametric(
                name="iridium-66",
                planes=6,
                slots=11,
                raan_spacing_deg=31.6,
                altitude_km=780,
                inclination_deg=86.4,
                pattern="walker-star",
            )
        )
        assert len(sats) == 66

    def test_iridium_66_raan_spread(self):
        config = _parametric(
            name="iridium-66",
            planes=6,
            slots=11,
            raan_spacing_deg=31.6,
            altitude_km=780,
            inclination_deg=86.4,
            pattern="walker-star",
        )
        assert isinstance(config, ParametricConstellation)
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert abs(raan_spread - 189.6) < 0.1
        assert raan_spread < 360.0

    def test_terminal_counts(self):
        sats = _expand(_parametric())
        for sat in sats:
            assert sat.isl_terminal_count == 4
            assert sat.ground_terminal_count == 1


class TestExplicitExpansion:
    def test_custom_example_count(self):
        assert len(_expand(_explicit())) == 4

    def test_custom_example_planes(self):
        sats = _expand(_explicit())
        assert {s.plane for s in sats} == {0, 1}

    def test_custom_example_orbital_elements(self):
        sats = _expand(_explicit())
        p0s0 = next(s for s in sats if s.plane == 0 and s.slot == 0)
        assert abs(p0s0.elements.semi_major_axis_km - (EARTH_RADIUS_KM + 550.0)) < 0.01
        assert abs(p0s0.elements.inclination_rad - math.radians(53.0)) < 1e-10
        assert abs(p0s0.elements.raan_rad) < 1e-10
        assert abs(p0s0.elements.true_anomaly_rad) < 1e-10

    def test_custom_example_terminal_counts(self):
        sats = _expand(_explicit())
        for sat in sats:
            assert sat.isl_terminal_count == 2
            assert sat.ground_terminal_count == 1


class TestTLEExpansion:
    def test_tle_expands_records_with_original_lines(self):
        config = adapter.validate_python(
            {
                "mode": "tle",
                "name": "test-tle",
                "tle_file": str(FIXTURES_DIR / "tles/sample.tle"),
                "default_terminals": _terminal_config(2),
                "filter": {"norad_ids": [25544, 23455]},
            }
        )

        sats = _expand(config)

        assert len(sats) == 2
        assert [sat.norad_id for sat in sats] == [25544, 23455]
        assert [sat.slot for sat in sats] == [0, 1]
        assert all(sat.plane == 0 for sat in sats)
        assert all(sat.tle_line_1 and sat.tle_line_1.startswith("1 ") for sat in sats)
        assert all(sat.tle_line_2 and sat.tle_line_2.startswith("2 ") for sat in sats)
        assert all(sat.isl_terminal_count == 2 for sat in sats)
        assert all(sat.ground_terminal_count == 1 for sat in sats)
        assert all(sat.elements.semi_major_axis_km > 6500 for sat in sats)
