"""Orbit-epoch anchoring: elements are a photograph at a declared instant.

The dt propagation model requires elements valid at the pacing epoch.
These tests pin the advance helper that restores that contract whenever
the pacing epoch is set: composition with the propagators, the dt == 0
identity that keeps continuous play bit-identical, and the refusal for
propagators that carry their own epoch.
"""

import math

import pytest
from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import (
    advance_mean_elements,
    propagate_eci_for_body,
    propagate_eci_j2_mean_elements_for_body,
)

MU_LUNA = 4902.800118
MU_EARTH = 398600.4418

LUNA_FRAME = BodyFrame(
    name="luna",
    mean_radius_km=1737.4,
    equatorial_radius_km=1738.1,
    polar_radius_km=1736.0,
    rotation_rate_rad_s=2.6617e-6,
    gravitational_parameter_km3_s2=MU_LUNA,
    j2=2.033e-4,
)

EARTH_FRAME = BodyFrame(
    name="earth",
    mean_radius_km=6371.0,
    equatorial_radius_km=6378.137,
    polar_radius_km=6356.752,
    rotation_rate_rad_s=7.2921159e-5,
    gravitational_parameter_km3_s2=MU_EARTH,
    j2=1.08262668e-3,
)

# The shipped ELFO relay orbit: the eccentric case that exposed the defect.
ELFO = OrbitalElements(
    semi_major_axis_km=(673.0 + 7332.0) / 2 + 1737.4,
    inclination_rad=math.radians(46.8),
    raan_rad=math.radians(252.0),
    eccentricity=(7332.0 - 673.0) / (7332.0 + 673.0 + 2 * 1737.4),
    argument_of_perigee_rad=math.radians(86.2),
    mean_anomaly_rad=math.radians(180.0),
)

LEO = OrbitalElements(
    semi_major_axis_km=6371.0 + 780.0,
    inclination_rad=math.radians(86.4),
    raan_rad=math.radians(31.6),
    eccentricity=0.0,
    argument_of_perigee_rad=0.0,
    mean_anomaly_rad=math.radians(45.0),
)


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class TestAdvanceComposition:
    """Advancing by a then propagating b equals propagating a + b."""

    @pytest.mark.parametrize("split_s", [1.0, 3600.0, 19509.0, 86400.0 * 3])
    def test_two_body_advance_then_propagate_matches_single_propagation(self, split_s):
        remainder_s = 1234.5
        direct, _ = propagate_eci_for_body(ELFO, split_s + remainder_s, mu_km3_s2=MU_LUNA)
        advanced = advance_mean_elements(
            ELFO, split_s, body_frame=LUNA_FRAME, propagator_id="two-body"
        )
        composed, _ = propagate_eci_for_body(advanced, remainder_s, mu_km3_s2=MU_LUNA)
        assert _distance(direct, composed) < 1e-6

    @pytest.mark.parametrize("split_s", [1.0, 3600.0, 5700.0, 86400.0])
    def test_j2_advance_then_propagate_matches_single_propagation(self, split_s):
        remainder_s = 987.0
        direct, _ = propagate_eci_j2_mean_elements_for_body(
            LEO, split_s + remainder_s, body_frame=EARTH_FRAME
        )
        advanced = advance_mean_elements(
            LEO, split_s, body_frame=EARTH_FRAME, propagator_id="j2-mean-elements"
        )
        composed, _ = propagate_eci_j2_mean_elements_for_body(
            advanced, remainder_s, body_frame=EARTH_FRAME
        )
        assert _distance(direct, composed) < 1e-6

    def test_the_defect_this_helper_repairs(self):
        """Perilune reached by advance differs from the epoch photograph.

        The pre-fix seek path used the authored photograph for any target
        instant. Half a period past the ELFO's apolune epoch the vehicle is
        at perilune, thousands of kilometers away from that photograph.
        """
        period_s = 2 * math.pi * math.sqrt(ELFO.semi_major_axis_km**3 / MU_LUNA)
        at_perilune = advance_mean_elements(
            ELFO, period_s / 2, body_frame=LUNA_FRAME, propagator_id="two-body"
        )
        photo_pos, _ = propagate_eci_for_body(ELFO, 0.0, mu_km3_s2=MU_LUNA)
        perilune_pos, _ = propagate_eci_for_body(at_perilune, 0.0, mu_km3_s2=MU_LUNA)
        assert _distance(photo_pos, perilune_pos) > 6000.0


class TestAdvanceContract:
    def test_zero_dt_is_the_identity_object(self):
        assert (
            advance_mean_elements(ELFO, 0.0, body_frame=LUNA_FRAME, propagator_id="two-body")
            is ELFO
        )

    def test_angles_stay_wrapped_over_long_advances(self):
        advanced = advance_mean_elements(
            LEO,
            86400.0 * 365,
            body_frame=EARTH_FRAME,
            propagator_id="j2-mean-elements",
        )
        for angle in (
            advanced.mean_anomaly_rad,
            advanced.raan_rad,
            advanced.argument_of_perigee_rad,
        ):
            assert 0.0 <= angle < math.tau

    def test_negative_dt_advances_backward(self):
        forward = advance_mean_elements(
            ELFO, 3600.0, body_frame=LUNA_FRAME, propagator_id="two-body"
        )
        back = advance_mean_elements(
            forward, -3600.0, body_frame=LUNA_FRAME, propagator_id="two-body"
        )
        assert abs(back.mean_anomaly_rad - ELFO.mean_anomaly_rad) < 1e-9

    def test_shape_fields_never_change(self):
        advanced = advance_mean_elements(
            LEO, 7200.0, body_frame=EARTH_FRAME, propagator_id="j2-mean-elements"
        )
        assert advanced.semi_major_axis_km == LEO.semi_major_axis_km
        assert advanced.eccentricity == LEO.eccentricity
        assert advanced.inclination_rad == LEO.inclination_rad

    def test_sgp4_is_refused_loudly(self):
        with pytest.raises(ValueError, match="carry their own epoch"):
            advance_mean_elements(LEO, 60.0, body_frame=EARTH_FRAME, propagator_id="sgp4-tle")


class TestRetargetSatellites:
    """The engine seam: working elements re-anchored at epoch changes."""

    def _sat(self, **overrides):
        from nodalarc.ome_runtime import SatelliteNode

        kwargs = {
            "plane": 0,
            "slot": 0,
            "elements": ELFO,
            "isl_terminal_count": 0,
            "ground_terminal_count": 0,
            "node_id": "luna-relay-sat-p00s00",
            "central_body": "luna",
            "propagator_id": "two-body",
            "authored_elements": ELFO,
            "authored_epoch_unix": 1_780_000_000.0,
        }
        kwargs.update(overrides)
        return SatelliteNode(**kwargs)

    def test_seek_equals_play_through_the_body_fixed_pipeline(self):
        """Position at T is identical whether reached by play or by seek."""
        from nodalarc.propagator import propagate_keplerian_for_body
        from ome.propagation_engine import retarget_satellites

        t0 = 1_780_000_000.0
        target = t0 + 19_509.0  # half the ELFO period past the epoch
        probe = target + 600.0

        played = self._sat()
        retarget_satellites(
            [played],
            session_propagator_id="two-body",
            anchor_epoch_unix=t0,
            body_frames={"luna": LUNA_FRAME},
        )
        pos_played, _, _, _, _ = propagate_keplerian_for_body(
            played.elements, t0, probe - t0, body_frame=LUNA_FRAME
        )

        sought = self._sat()
        retarget_satellites(
            [sought],
            session_propagator_id="two-body",
            anchor_epoch_unix=target,
            body_frames={"luna": LUNA_FRAME},
        )
        pos_sought, _, _, _, _ = propagate_keplerian_for_body(
            sought.elements, target, probe - target, body_frame=LUNA_FRAME
        )

        assert _distance(pos_played, pos_sought) < 1e-6

    def test_repeated_retargets_never_accumulate(self):
        from ome.propagation_engine import retarget_satellites

        sat = self._sat()
        frames = {"luna": LUNA_FRAME}
        for anchor in (1_780_050_000.0, 1_780_003_600.0, 1_780_000_000.0):
            retarget_satellites(
                [sat],
                session_propagator_id="two-body",
                anchor_epoch_unix=anchor,
                body_frames=frames,
            )
        # Back at the authored epoch the working photograph IS the authored
        # object: derivation is from authored facts, never from the previous
        # working state.
        assert sat.elements is ELFO

    def test_missing_authored_photograph_refuses(self):
        from ome.propagation_engine import retarget_satellites

        sat = self._sat(authored_elements=None, authored_epoch_unix=None)
        with pytest.raises(ValueError, match="authored element photograph"):
            retarget_satellites(
                [sat],
                session_propagator_id="two-body",
                anchor_epoch_unix=1_780_000_100.0,
                body_frames={"luna": LUNA_FRAME},
            )

    def test_sgp4_satellites_are_left_alone(self):
        from ome.propagation_engine import retarget_satellites

        sat = self._sat(propagator_id="sgp4-tle", authored_elements=None, authored_epoch_unix=None)
        retarget_satellites(
            [sat],
            session_propagator_id="sgp4-tle",
            anchor_epoch_unix=1_780_099_999.0,
            body_frames={},
        )
        assert sat.elements is ELFO

    def test_missing_body_frame_refuses(self):
        from ome.propagation_engine import retarget_satellites

        sat = self._sat()
        with pytest.raises(ValueError, match="missing body frame"):
            retarget_satellites(
                [sat],
                session_propagator_id="two-body",
                anchor_epoch_unix=1_780_000_100.0,
                body_frames={},
            )
