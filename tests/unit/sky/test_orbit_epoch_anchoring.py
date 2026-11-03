"""Orbit-epoch anchoring: elements are a photograph at a declared instant.

The dt propagation model requires elements valid at the pacing epoch.
These tests pin the advance helper that restores that contract whenever
the pacing epoch is set: composition with the propagators, the dt == 0
identity that keeps continuous play bit-identical, and the refusal for
propagators that carry their own epoch.
"""

import math

import numpy as np
import pytest
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import Vec3
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import ElementsBatch, advance_mean_elements, orbit_states
from nodalarc.physics.satellites import analytic_satellite_states, orbit_model_for

from tests.catalog_session_fixtures import shipped_read_view
from tests.physics_fixtures import EARTH_ORIGIN_BODY_STATES, physics_for

MU_LUNA = 4902.800118
MU_EARTH = 398600.4418

LUNA_FRAME = BodyFrame(
    name="luna",
    mean_radius_km=1737.4,
    equatorial_radius_km=1738.1,
    polar_radius_km=1736.0,
    gravitational_parameter_km3_s2=MU_LUNA,
    j2=2.033e-4,
)

EARTH_FRAME = BodyFrame(
    name="earth",
    mean_radius_km=6371.0,
    equatorial_radius_km=6378.137,
    polar_radius_km=6356.752,
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


def _inertial(elements, dt, *, body_frame, model):
    """Body-centered inertial state from the production orbit kernel."""
    st = orbit_states(
        ElementsBatch.from_elements([elements]), np.array([dt]), model=model, body_frame=body_frame
    )
    return (
        Vec3(float(st.px[0, 0]), float(st.py[0, 0]), float(st.pz[0, 0])),
        Vec3(float(st.vx[0, 0]), float(st.vy[0, 0]), float(st.vz[0, 0])),
    )


def _body_fixed(elements, epoch, dt, *, body_frame, model):
    """Body-fixed position at epoch + dt through the shared satellite path."""
    states = analytic_satellite_states(
        physics_for(body_frame.name),
        body_frame=body_frame,
        model=model,
        elements=[elements],
        phases_s=[dt],
        master_sim_time_unix=epoch + dt,
    )
    return Vec3(*map(float, states.position_fixed_km[0]))


class TestAdvanceComposition:
    """Advancing by a then propagating b equals propagating a + b."""

    @pytest.mark.parametrize("split_s", [1.0, 3600.0, 19509.0, 86400.0 * 3])
    def test_two_body_advance_then_propagate_matches_single_propagation(self, split_s):
        remainder_s = 1234.5
        direct, _ = _inertial(ELFO, split_s + remainder_s, body_frame=LUNA_FRAME, model="two-body")
        advanced = advance_mean_elements(ELFO, split_s, body_frame=LUNA_FRAME, model="two-body")
        composed, _ = _inertial(advanced, remainder_s, body_frame=LUNA_FRAME, model="two-body")
        assert _distance(direct, composed) < 1e-6

    @pytest.mark.parametrize("split_s", [1.0, 3600.0, 5700.0, 86400.0])
    def test_j2_advance_then_propagate_matches_single_propagation(self, split_s):
        remainder_s = 987.0
        direct, _ = _inertial(
            LEO, split_s + remainder_s, body_frame=EARTH_FRAME, model="j2-mean-elements"
        )
        advanced = advance_mean_elements(
            LEO, split_s, body_frame=EARTH_FRAME, model="j2-mean-elements"
        )
        composed, _ = _inertial(
            advanced, remainder_s, body_frame=EARTH_FRAME, model="j2-mean-elements"
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
            ELFO, period_s / 2, body_frame=LUNA_FRAME, model="two-body"
        )
        photo_pos, _ = _inertial(ELFO, 0.0, body_frame=LUNA_FRAME, model="two-body")
        perilune_pos, _ = _inertial(at_perilune, 0.0, body_frame=LUNA_FRAME, model="two-body")
        assert _distance(photo_pos, perilune_pos) > 6000.0


class TestAdvanceContract:
    def test_zero_dt_is_the_identity_object(self):
        assert advance_mean_elements(ELFO, 0.0, body_frame=LUNA_FRAME, model="two-body") is ELFO

    def test_angles_stay_wrapped_over_long_advances(self):
        advanced = advance_mean_elements(
            LEO,
            86400.0 * 365,
            body_frame=EARTH_FRAME,
            model="j2-mean-elements",
        )
        for angle in (
            advanced.mean_anomaly_rad,
            advanced.raan_rad,
            advanced.argument_of_perigee_rad,
        ):
            assert 0.0 <= angle < math.tau

    def test_negative_dt_advances_backward(self):
        forward = advance_mean_elements(ELFO, 3600.0, body_frame=LUNA_FRAME, model="two-body")
        back = advance_mean_elements(forward, -3600.0, body_frame=LUNA_FRAME, model="two-body")
        assert abs(back.mean_anomaly_rad - ELFO.mean_anomaly_rad) < 1e-9

    def test_shape_fields_never_change(self):
        advanced = advance_mean_elements(
            LEO, 7200.0, body_frame=EARTH_FRAME, model="j2-mean-elements"
        )
        assert advanced.semi_major_axis_km == LEO.semi_major_axis_km
        assert advanced.eccentricity == LEO.eccentricity
        assert advanced.inclination_rad == LEO.inclination_rad

    def test_sgp4_is_refused_loudly(self):
        # TLE elements carry their own epoch and have no analytic model to advance.
        with pytest.raises(ValueError, match="no analytic orbit model"):
            orbit_model_for("sgp4-tle")


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
        from nodalarc.ome_runtime import retarget_satellites

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
        pos_played = _body_fixed(
            played.elements, t0, probe - t0, body_frame=LUNA_FRAME, model="two-body"
        )

        sought = self._sat()
        retarget_satellites(
            [sought],
            session_propagator_id="two-body",
            anchor_epoch_unix=target,
            body_frames={"luna": LUNA_FRAME},
        )
        pos_sought = _body_fixed(
            sought.elements, target, probe - target, body_frame=LUNA_FRAME, model="two-body"
        )

        assert _distance(pos_played, pos_sought) < 1e-6

    def test_repeated_retargets_never_accumulate(self):
        from nodalarc.ome_runtime import retarget_satellites

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
        from nodalarc.ome_runtime import retarget_satellites

        sat = self._sat(authored_elements=None, authored_epoch_unix=None)
        with pytest.raises(ValueError, match="authored element photograph"):
            retarget_satellites(
                [sat],
                session_propagator_id="two-body",
                anchor_epoch_unix=1_780_000_100.0,
                body_frames={"luna": LUNA_FRAME},
            )

    def test_sgp4_satellites_are_left_alone(self):
        from nodalarc.ome_runtime import retarget_satellites

        sat = self._sat(propagator_id="sgp4-tle", authored_elements=None, authored_epoch_unix=None)
        retarget_satellites(
            [sat],
            session_propagator_id="sgp4-tle",
            anchor_epoch_unix=1_780_099_999.0,
            body_frames={},
        )
        assert sat.elements is ELFO

    def test_missing_body_frame_refuses(self):
        from nodalarc.ome_runtime import retarget_satellites

        sat = self._sat()
        with pytest.raises(ValueError, match="missing body frame"):
            retarget_satellites(
                [sat],
                session_propagator_id="two-body",
                anchor_epoch_unix=1_780_000_100.0,
                body_frames={},
            )


class TestConstructionAuthority:
    """Anchoring happens where inputs are built, so every consumer inherits.

    Live pacing, the batch timeline, and the builder preview all take
    satellites from build_ome_inputs_from_resolved and
    propagate dt from an epoch they supply. The working photograph must be
    valid at the session epoch the moment inputs exist, not only after the
    live loop re-anchors.
    """

    @staticmethod
    def _inputs(start_time=None):
        from pathlib import Path

        from nodalarc.configuration_yaml import load_configuration_yaml
        from nodalarc.ome_inputs import build_ome_inputs_from_resolved
        from nodalarc.resolve_session import resolve_session

        root = Path(__file__).resolve().parents[3]
        document = load_configuration_yaml(
            (root / "catalog/nodalarc/sessions/earth-leo-simple.yaml").read_bytes()
        )
        if start_time is not None:
            document["time"]["start_time"] = start_time
        return build_ome_inputs_from_resolved(
            resolve_session(document, catalog=shipped_read_view())
        )

    def test_matching_epochs_keep_the_authored_object(self):
        # The ring orbit's declared epoch.
        inputs = self._inputs(start_time="2026-06-08T00:00:00Z")
        for sat in inputs.satellites:
            assert sat.elements is sat.authored_elements

    def test_shifted_start_is_anchored_against_textbook_rates(self):
        """One hour past the orbit epoch, verified against re-derived rates.

        The expected angles are computed here from first principles (mean
        motion and standard first-order J2 secular rates), independently of
        the production advance helper, using the same body facts the
        session resolved.
        """
        inputs = self._inputs(start_time="2026-06-08T01:00:00Z")
        frame = inputs.body_frames["earth"]
        shifted_s = 3600.0

        for sat in inputs.satellites:
            authored = sat.authored_elements
            a = authored.semi_major_axis_km
            e = authored.eccentricity
            i = authored.inclination_rad
            n = math.sqrt(frame.gravitational_parameter_km3_s2 / a**3)
            p = a * (1.0 - e * e)
            j2_factor = 1.5 * frame.j2 * (frame.equatorial_radius_km / p) ** 2 * n
            raan_dot = -j2_factor * math.cos(i)
            # NodalArc's circular J2 contract: perigee is undefined at e = 0,
            # so its secular rate is zero there. The kernel and the advance
            # helper share the convention; the test honors it.
            argp_dot = 0.0 if e == 0.0 else j2_factor * (2.0 - 2.5 * math.sin(i) ** 2)
            m_dot = n + j2_factor * math.sqrt(1.0 - e * e) * (1.0 - 1.5 * math.sin(i) ** 2)

            expected_m = (authored.mean_anomaly_rad + m_dot * shifted_s) % math.tau
            expected_raan = (authored.raan_rad + raan_dot * shifted_s) % math.tau
            expected_argp = (authored.argument_of_perigee_rad + argp_dot * shifted_s) % math.tau

            assert sat.elements is not authored
            assert abs(sat.elements.mean_anomaly_rad - expected_m) < 1e-9
            assert abs(sat.elements.raan_rad - expected_raan) < 1e-9
            assert abs(sat.elements.argument_of_perigee_rad - expected_argp) < 1e-9

    def test_the_authored_photograph_is_never_mutated(self):
        inputs = self._inputs(start_time="2026-06-08T01:00:00Z")
        baseline = self._inputs()
        for shifted, plain in zip(inputs.satellites, baseline.satellites):
            assert (
                shifted.authored_elements.mean_anomaly_rad
                == plain.authored_elements.mean_anomaly_rad
            )
            assert shifted.authored_elements.raan_rad == plain.authored_elements.raan_rad


class TestWindowAuthority:
    """A precompute window's epoch can never re-interpret the elements.

    Propagation derives each satellite's phase time from the working
    photograph's own validity epoch, so a window beginning after session
    start describes the same physical world as continuous play at the same
    absolute instant.
    """

    def test_later_window_matches_continuous_play_at_the_same_instant(self):
        from ome.propagation_engine import propagate_satellites

        inputs = TestConstructionAuthority._inputs()
        from pathlib import Path

        from nodalarc.configuration_yaml import load_configuration_yaml

        root = Path(__file__).resolve().parents[3]
        document = load_configuration_yaml(
            (root / "catalog/nodalarc/sessions/earth-leo-simple.yaml").read_bytes()
        )
        from datetime import datetime

        start = datetime.fromisoformat(
            document["time"]["start_time"].replace("Z", "+00:00")
        ).timestamp()
        window_epoch = start + 3600.0
        probe_dt = 25.0
        absolute = window_epoch + probe_dt

        body_states = inputs.physics.body_states(inputs.active_bodies, absolute)
        continuous = propagate_satellites(
            satellites=inputs.satellites,
            addressing=inputs.addressing,
            epoch_unix=start,
            dt=absolute - start,
            propagator_id=inputs.propagator_id,
            body_states=body_states,
            body_frames=inputs.body_frames,
            physics=inputs.physics,
        )
        windowed = propagate_satellites(
            satellites=inputs.satellites,
            addressing=inputs.addressing,
            epoch_unix=window_epoch,
            dt=probe_dt,
            propagator_id=inputs.propagator_id,
            body_states=body_states,
            body_frames=inputs.body_frames,
            physics=inputs.physics,
        )
        for node_id, state in continuous.items():
            error_km = _distance(state.position_ecef_km, windowed[node_id].position_ecef_km)
            assert error_km < 1e-6, f"{node_id}: {error_km} km"

    def test_later_window_matches_an_independently_anchored_reference(self):
        """The full window path against an independent reference world.

        Side A: session-start elements carried into a later window by the
        anchor-corrected engine. Side B: a separately constructed snapshot
        whose session starts at that later epoch, so its elements were
        derived at construction through the authored path. Same absolute
        instants, two derivations, one physical world: complete typed event
        payloads within tolerance, and identical ISL state, ground state,
        associations, and pending teardowns.
        """
        from datetime import datetime
        from pathlib import Path

        from nodalarc.configuration_yaml import load_configuration_yaml
        from ome.event_stream import precompute_timeline_window

        root = Path(__file__).resolve().parents[3]
        document = load_configuration_yaml(
            (root / "catalog/nodalarc/sessions/earth-leo-simple.yaml").read_bytes()
        )
        start = datetime.fromisoformat(
            document["time"]["start_time"].replace("Z", "+00:00")
        ).timestamp()
        shifted_iso = "2026-06-08T01:00:00Z"
        window_epoch = start + 3600.0

        inputs_a = TestConstructionAuthority._inputs()
        inputs_b = TestConstructionAuthority._inputs(start_time=shifted_iso)

        def window(inputs):
            return precompute_timeline_window(
                satellites=inputs.satellites,
                addressing=inputs.addressing,
                gs_file=inputs.gs_file,
                neighbors=inputs.neighbors,
                epoch_unix=window_epoch,
                duration_s=30.0,
                propagator_id=inputs.propagator_id,
                ground_scheduling=inputs.ground_scheduling,
                ground_defaults_applied=True,
                ground_candidate_satellites_by_gs=(inputs.ground_candidate_satellites_by_gs),
                physics=inputs.physics,
                body_frames=inputs.body_frames,
                active_bodies=inputs.active_bodies,
            )

        carried = window(inputs_a)
        reference = window(inputs_b)

        assert carried.isl_state == reference.isl_state
        assert carried.gs_state == reference.gs_state
        assert carried.associations == reference.associations
        assert carried.pending_teardowns == reference.pending_teardowns

        assert len(carried.events) == len(reference.events)
        for got, want in zip(carried.events, reference.events):
            assert got.timestamp_s == want.timestamp_s
            assert got.event_type == want.event_type
            _assert_payloads_close(got.data, want.data)


def _assert_payloads_close(got, want, path="event"):
    """Complete typed payload comparison with numeric tolerance.

    Construction-versus-advance derivations agree to roughly 2.5e-11 km;
    floats compare at 1e-6 absolute as a generic noise guard.
    """
    if isinstance(got, float) or isinstance(want, float):
        assert got == pytest.approx(want, abs=1e-6), path
        return
    if hasattr(got, "model_dump"):
        assert type(got) is type(want), path
        _assert_payloads_close(got.model_dump(), want.model_dump(), path)
        return
    if isinstance(got, dict):
        assert set(got) == set(want), path
        for key in got:
            _assert_payloads_close(got[key], want[key], f"{path}.{key}")
        return
    if isinstance(got, (list, tuple)):
        assert len(got) == len(want), path
        for index, (a, b) in enumerate(zip(got, want)):
            _assert_payloads_close(a, b, f"{path}[{index}]")
        return
    assert got == want, path


class TestPropagationRefusals:
    """Both refusal contracts hold permanently."""

    def test_unanchored_keplerian_elements_are_refused(self):
        from nodalarc.ome_runtime import SatelliteNode
        from ome.propagation_engine import propagate_satellites

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        sat = SatelliteNode(
            plane=0,
            slot=0,
            elements=LEO,
            isl_terminal_count=0,
            ground_terminal_count=0,
            node_id="earth-leo-sat-p00s00",
            central_body="earth",
            propagator_id="two-body",
        )
        from nodalarc.frames import Vec3
        from nodalarc.physics import BodyState

        body_states = {
            "earth": BodyState(
                body_id="earth",
                position_km=Vec3(0.0, 0.0, 0.0),
                velocity_km_s=Vec3(0.0, 0.0, 0.0),
            )
        }
        with pytest.raises(
            ValueError,
            match="earth-leo-sat-p00s00.*carry no validity epoch",
        ):
            propagate_satellites(
                satellites=[sat],
                addressing=StaticOmeAddressing(),
                epoch_unix=1_780_876_800.0,
                dt=0.0,
                propagator_id="two-body",
                body_frames={"earth": EARTH_FRAME},
                body_states=body_states,
                physics=physics_for("earth"),
            )

    def test_nonuniform_j2_anchors_each_propagate_from_their_own_anchor(self):
        from nodalarc.ome_runtime import SatelliteNode
        from ome.propagation_engine import propagate_satellites

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        def sat(slot, anchor):
            return SatelliteNode(
                plane=0,
                slot=slot,
                elements=LEO,
                elements_epoch_unix=anchor,
                isl_terminal_count=0,
                ground_terminal_count=0,
                node_id=f"earth-leo-sat-p00s{slot:02d}",
                central_body="earth",
                propagator_id="j2-mean-elements",
            )

        epoch = 1_780_876_800.0
        anchors = (epoch, epoch + 3600.0)
        states = propagate_satellites(
            satellites=[sat(0, anchors[0]), sat(1, anchors[1])],
            addressing=StaticOmeAddressing(),
            epoch_unix=epoch,
            dt=0.0,
            propagator_id="j2-mean-elements",
            body_frames={"earth": EARTH_FRAME},
            body_states=EARTH_ORIGIN_BODY_STATES,
            physics=physics_for("earth"),
        )
        for slot, anchor in enumerate(anchors):
            expected, _ = _inertial(
                LEO, epoch - anchor, body_frame=EARTH_FRAME, model="j2-mean-elements"
            )
            got = states[f"earth-leo-sat-p00s{slot:02d}"].position_common_km
            assert _distance(got, expected) < 1e-9

    def test_fractional_mismatched_call_matches_aligned_reference(self):
        from nodalarc.frames import Vec3
        from nodalarc.ome_runtime import retarget_satellites
        from nodalarc.physics import BodyState
        from ome.propagation_engine import propagate_satellites

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        anchor = 1_780_876_800.0
        fractional_epoch = anchor + 3600.25
        fractional_dt = 0.3

        def sat(propagator):
            from nodalarc.ome_runtime import SatelliteNode

            return SatelliteNode(
                plane=0,
                slot=0,
                elements=LEO,
                elements_epoch_unix=anchor,
                authored_elements=LEO,
                authored_epoch_unix=anchor,
                isl_terminal_count=0,
                ground_terminal_count=0,
                node_id="earth-leo-sat-p00s00",
                central_body="earth",
                propagator_id=propagator,
            )

        body_states = {
            "earth": BodyState(
                body_id="earth",
                position_km=Vec3(0.0, 0.0, 0.0),
                velocity_km_s=Vec3(0.0, 0.0, 0.0),
            )
        }

        for propagator in ("two-body", "j2-mean-elements"):
            mismatched = sat(propagator)
            aligned = sat(propagator)
            retarget_satellites(
                [aligned],
                session_propagator_id=propagator,
                anchor_epoch_unix=fractional_epoch,
                body_frames={"earth": EARTH_FRAME},
            )
            kwargs = {
                "addressing": StaticOmeAddressing(),
                "epoch_unix": fractional_epoch,
                "dt": fractional_dt,
                "propagator_id": propagator,
                "body_frames": {"earth": EARTH_FRAME},
                "body_states": body_states,
                "physics": physics_for("earth"),
            }
            got = propagate_satellites(satellites=[mismatched], **kwargs)
            want = propagate_satellites(satellites=[aligned], **kwargs)
            state_got = got["earth-leo-sat-p00s00"]
            state_want = want["earth-leo-sat-p00s00"]
            reported = state_got.sim_time_unix
            assert reported == fractional_epoch + fractional_dt
            assert state_want.sim_time_unix == reported

            # The constraint, for BOTH states: each is the state of its own
            # (elements, anchor) pair at the one reported instant, spelled
            # independently through the lib primitives.
            def primitive_fixed(elements, own_anchor):
                pos_inertial, _ = _inertial(
                    elements,
                    reported - own_anchor,
                    body_frame=EARTH_FRAME,
                    model=orbit_model_for(propagator),
                )
                rotation = physics_for("earth").orientation("earth", reported).rotation
                return Vec3(*map(float, rotation @ np.array(pos_inertial)))

            got_error_km = _distance(state_got.position_ecef_km, primitive_fixed(LEO, anchor))
            assert got_error_km < 1e-12, f"{propagator}: {got_error_km} km"
            want_error_km = _distance(
                state_want.position_ecef_km,
                primitive_fixed(aligned.elements, aligned.elements_epoch_unix),
            )
            assert want_error_km < 1e-12, f"{propagator}: {want_error_km} km"

            # One reported float, one canonical phase authority: the two
            # derivations now differ only by element-advance composition
            # rounding. The 1e-9 km threshold fits THIS fixture's one-hour
            # offset; composition rounding grows with the advance span
            # (measured near 1e-6 km for far-future anchors) and is not a
            # global error bound. Each state matching its own authority at
            # 1e-12 km above is the invariant.
            drift_km = _distance(state_got.position_ecef_km, state_want.position_ecef_km)
            assert drift_km < 1e-9, f"{propagator}: {drift_km} km"
