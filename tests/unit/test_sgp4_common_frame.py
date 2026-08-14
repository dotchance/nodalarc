"""SGP4 common-frame authority: ITRS for same-body, GCRS for cross-body.

The SGP4 boundary evaluates Skyfield once and retains both frames. The
common-frame slots of PropagatedState are runtime-typed and refuse
relabeled vectors. Goldens pin both frames at the ISS fixture epoch and
twelve hours later, where the frames' axes are nearly opposed and the
old aliasing produced a 12,709 km error, a false range, and a false
los_blocked verdict.

There is no frontend SGP4 twin to cover: the sim worker propagates only
Keplerian/J2 elements and TLE rendering is driven by OME snapshot
geodetic positions, body-local and presentation-only.
"""

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import CommonVec3, EcefVec3, Vec3
from nodalarc.geo import compute_latency_ms
from nodalarc.propagator import propagate_sgp4_tle, propagate_sgp4_tle_states

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
ISS_TLE_EPOCH_UNIX = 1615896900.000275

EARTH_FRAME = BodyFrame(
    name="earth",
    mean_radius_km=6371.0,
    equatorial_radius_km=6378.137,
    polar_radius_km=6356.752,
    rotation_rate_rad_s=7.2921158553e-5,
    gravitational_parameter_km3_s2=398600.4418,
    j2=1.08262668e-3,
)

# Goldens from the single-evaluation Skyfield boundary. Skyfield and sgp4
# are lockfile-pinned; a dependency upgrade that moves these numbers must
# arrive as a deliberate diff of this table, not be absorbed by tolerance.
GOLDEN = {
    0.0: {
        "itrs_pos": (-4329.375350762542, 2211.9930425759426, 4740.40568912658),
        "itrs_vel": (-5.240188571438462, -4.385887860221932, -2.731355094043767),
        "gcrs_pos": (-4231.119851995798, 2377.5438181736145, 4748.974672117138),
        "gcrs_vel": (-5.5845138643634815, -4.489569376632045, -2.719985233454282),
        "separation_km": 192.703476,
    },
    43200.0: {
        "itrs_pos": (-5006.790360510804, -3911.0864939752532, 2409.80449805389),
        "itrs_vel": (4.45404594877855, -2.384642850059607, 5.357169687912351),
        "gcrs_pos": (5126.795151947043, 3759.0822575731286, 2399.3701354879504),
        "gcrs_vel": (-4.6437071007668305, 2.890797125620803, 5.366572005142366),
        "separation_km": 12709.097249,
    },
}

POS_TOL_KM = 1e-6
VEL_TOL_KMS = 1e-9


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class TestBoundaryGoldens:
    """One Skyfield evaluation, both frames, pinned in both instants."""

    @pytest.mark.parametrize("dt_s", [0.0, 43200.0])
    def test_both_frames_match_goldens(self, dt_s):
        states = propagate_sgp4_tle_states(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            dt_s,
            body_frame=EARTH_FRAME,
        )
        golden = GOLDEN[dt_s]
        assert _distance(states.position_itrs, golden["itrs_pos"]) < POS_TOL_KM
        assert _distance(states.velocity_itrs, golden["itrs_vel"]) < VEL_TOL_KMS
        assert _distance(states.position_gcrs, golden["gcrs_pos"]) < POS_TOL_KM
        assert _distance(states.velocity_gcrs, golden["gcrs_vel"]) < VEL_TOL_KMS

    @pytest.mark.parametrize("dt_s", [0.0, 43200.0])
    def test_the_frames_are_genuinely_different(self, dt_s):
        states = propagate_sgp4_tle_states(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            dt_s,
            body_frame=EARTH_FRAME,
        )
        separation = _distance(states.position_itrs, states.position_gcrs)
        assert abs(separation - GOLDEN[dt_s]["separation_km"]) < 1e-3

    def test_the_two_body_fixed_realizations_disagree_by_thirty_km(self):
        """Why mixed same-body propagator populations are refused: one
        physical GCRS state lands 30.316 km apart when rotated by the
        session's simplified GMST model versus Skyfield's true ITRS. The
        pin documents the gap the frame-realization gate exists to keep
        out of same-body geometry."""
        from nodalarc.propagator import eci_to_body_fixed

        states = propagate_sgp4_tle_states(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            0.0,
            body_frame=EARTH_FRAME,
        )
        simplified = eci_to_body_fixed(Vec3(*states.position_gcrs), ISS_TLE_EPOCH_UNIX, EARTH_FRAME)
        gap_km = _distance(states.position_itrs, simplified)
        assert abs(gap_km - 30.316087395) < 1e-6

    def test_itrs_wrapper_is_unchanged(self):
        """The same-body contract every existing consumer keeps."""
        pos, vel, geo = propagate_sgp4_tle(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            0.0,
            body_frame=EARTH_FRAME,
        )
        assert _distance(pos, GOLDEN[0.0]["itrs_pos"]) < POS_TOL_KM
        assert _distance(vel, GOLDEN[0.0]["itrs_vel"]) < VEL_TOL_KMS
        assert -90.0 <= geo.lat_deg <= 90.0


class TestCommonFrameTyping:
    """Common-frame slots refuse relabeled vectors at runtime."""

    def test_relabeled_vec3_is_refused(self):
        from ome.propagation_engine import PropagatedState

        with pytest.raises(TypeError, match="requires CommonVec3"):
            PropagatedState(
                node_id="earth-leo-sat-p00s00",
                sim_time_unix=ISS_TLE_EPOCH_UNIX,
                position_ecef_km=EcefVec3(Vec3(1.0, 2.0, 3.0)),
                velocity_ecef_km_s=EcefVec3(Vec3(0.1, 0.2, 0.3)),
                geodetic=None,
                propagator_id="sgp4-tle",
                central_body="earth",
                position_common_km=EcefVec3(Vec3(1.0, 2.0, 3.0)),
                velocity_common_km_s=CommonVec3(0.1, 0.2, 0.3),
                body_origin_common_km=CommonVec3(0.0, 0.0, 0.0),
            )

    def test_constructed_common_vectors_pass(self):
        from ome.propagation_engine import PropagatedState

        state = PropagatedState(
            node_id="earth-leo-sat-p00s00",
            sim_time_unix=ISS_TLE_EPOCH_UNIX,
            position_ecef_km=EcefVec3(Vec3(1.0, 2.0, 3.0)),
            velocity_ecef_km_s=EcefVec3(Vec3(0.1, 0.2, 0.3)),
            geodetic=None,
            propagator_id="sgp4-tle",
            central_body="earth",
            position_common_km=CommonVec3(1.0, 2.0, 3.0),
            velocity_common_km_s=CommonVec3(0.1, 0.2, 0.3),
            body_origin_common_km=CommonVec3(0.0, 0.0, 0.0),
        )
        assert isinstance(state.position_common_km, CommonVec3)

    def test_composition_refuses_the_original_defect(self):
        """`pos_inertial = pos_ecef` must now raise, not compose."""
        from ome.propagation_engine import _common_vec

        origin = Vec3(300_000.0, 200_000.0, 100_000.0)
        pos_ecef = EcefVec3(Vec3(-4329.375, 2211.993, 4740.406))
        with pytest.raises(TypeError, match="requires GcrsVec3"):
            _common_vec(origin, pos_ecef)

    def test_composition_accepts_gcrs_provenance(self):
        from nodalarc.frames import GcrsVec3
        from ome.propagation_engine import _common_vec

        composed = _common_vec(Vec3(1.0, 2.0, 3.0), GcrsVec3(10.0, 20.0, 30.0))
        assert composed == CommonVec3(11.0, 22.0, 33.0)

    def test_boundary_gcrs_state_carries_provenance(self):
        from nodalarc.frames import GcrsVec3

        states = propagate_sgp4_tle_states(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            0.0,
            body_frame=EARTH_FRAME,
        )
        assert isinstance(states.position_gcrs, GcrsVec3)
        assert isinstance(states.velocity_gcrs, GcrsVec3)
        assert not isinstance(states.position_itrs, GcrsVec3)


def _earth_luna_world():
    """Real resolved earth-luna inputs plus the resolved session."""
    from nodalarc.configuration_yaml import load_configuration_yaml
    from nodalarc.ome_inputs import build_ome_inputs_from_resolved
    from nodalarc.resolve_session import resolve_session

    root = Path(__file__).resolve().parents[2]
    document = load_configuration_yaml(
        (root / "catalog/nodalarc/sessions/earth-luna-dtn.yaml").read_bytes()
    )
    resolved = resolve_session(document)
    return build_ome_inputs_from_resolved(resolved), resolved


def _tle_era_body_ephemeris(resolved):
    """The session's de440s kernel re-spanned around the ISS TLE era."""
    from nodalarc.ephemeris_runtime import SkyfieldBspEphemeris
    from nodalarc.ome_inputs import _runtime_ephemeris_config  # noqa: SLF001

    return SkyfieldBspEphemeris.from_config(
        _runtime_ephemeris_config(resolved),
        required_bodies=frozenset({"earth", "luna"}),
        epoch_unix=ISS_TLE_EPOCH_UNIX,
        end_epoch_unix=ISS_TLE_EPOCH_UNIX + 2 * 43200.0,
    )


class TestCrossBodyAuthority:
    """A real SGP4 Earth endpoint against a de440s lunar endpoint."""

    def _states_at(self, probe_unix):
        from nodalarc.ephemeris_runtime import body_states_at
        from nodalarc.ome_runtime import SatelliteNode, retarget_satellites
        from nodalarc.orbital import OrbitalElements
        from ome.propagation_engine import propagate_satellites

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        inputs, resolved = _earth_luna_world()
        ephemeris = _tle_era_body_ephemeris(resolved)
        body_states = body_states_at(ephemeris, {"earth", "luna"}, probe_unix)

        iss = SatelliteNode(
            plane=0,
            slot=0,
            elements=OrbitalElements(
                semi_major_axis_km=6798.0,
                inclination_rad=math.radians(51.6),
                raan_rad=0.0,
                eccentricity=0.0,
                argument_of_perigee_rad=0.0,
                mean_anomaly_rad=0.0,
            ),
            node_id="earth-iss-sat-p00s00",
            central_body="earth",
            propagator_id="sgp4-tle",
            tle_line_1=ISS_TLE_LINE_1,
            tle_line_2=ISS_TLE_LINE_2,
            norad_id=25544,
            isl_terminal_count=1,
            ground_terminal_count=0,
        )
        luna_frame = inputs.body_frames["luna"]
        luna_elements = OrbitalElements(
            semi_major_axis_km=(673.0 + 7332.0) / 2 + luna_frame.mean_radius_km,
            inclination_rad=math.radians(46.8),
            raan_rad=math.radians(252.0),
            eccentricity=(7332.0 - 673.0) / (7332.0 + 673.0 + 2 * luna_frame.mean_radius_km),
            argument_of_perigee_rad=math.radians(86.2),
            mean_anomaly_rad=math.radians(180.0),
        )
        relay = SatelliteNode(
            plane=0,
            slot=0,
            elements=luna_elements,
            authored_elements=luna_elements,
            authored_epoch_unix=probe_unix,
            node_id="luna-relay-sat-p00s00",
            central_body="luna",
            propagator_id="two-body",
            isl_terminal_count=1,
            ground_terminal_count=0,
        )
        retarget_satellites(
            [relay],
            session_propagator_id="mixed",
            anchor_epoch_unix=probe_unix,
            body_frames=inputs.body_frames,
        )
        states = propagate_satellites(
            satellites=[iss, relay],
            addressing=StaticOmeAddressing(),
            epoch_unix=probe_unix,
            dt=0.0,
            propagator_id="mixed",
            body_frames=inputs.body_frames,
            body_states=body_states,
        )
        return states, body_states, inputs

    def test_cross_body_range_latency_and_verdict(self):
        from nodalarc.models.addressing import NeighborAssignment
        from ome.isl_engine import IslTerminalConstraints, evaluate_isl_feasibility

        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, body_states, inputs = self._states_at(probe_unix)

        # Independent truth: boundary GCRS plus ephemeris origins, composed
        # by hand, never through the engine or the ISL machinery.
        boundary = propagate_sgp4_tle_states(
            ISS_TLE_LINE_1,
            ISS_TLE_LINE_2,
            ISS_TLE_EPOCH_UNIX,
            43200.0,
            body_frame=EARTH_FRAME,
        )
        earth_origin = body_states["earth"].position_km
        iss_common_truth = tuple(o + p for o, p in zip(earth_origin, boundary.position_gcrs))
        relay_common = states["luna-relay-sat-p00s00"].position_common_km
        expected_range_km = _distance(iss_common_truth, relay_common)

        # Production classification and limits: _isl_link_type maps every
        # inter-body satellite pair to cross_plane_isl, whose role gate
        # requires cross-plane terminals and whose tracking limit is
        # enforced. Limits are the shipped optical-cislunar-crosslink
        # terminal's (450,000 km, 0.25 deg/s).
        def _evaluate(max_tracking_rate_deg_s):
            constraints = IslTerminalConstraints(
                role="cross-plane",
                max_range_km=450_000.0,
                max_tracking_rate_deg_s=max_tracking_rate_deg_s,
                field_of_regard_deg=360.0,
                terminal_type="optical",
            )
            results = evaluate_isl_feasibility(
                node_order=["earth-iss-sat-p00s00", "luna-relay-sat-p00s00"],
                sat_states=states,
                by_node={
                    "earth-iss-sat-p00s00": [
                        NeighborAssignment("isl0", "luna-relay-sat-p00s00", "cross_plane_isl", 0)
                    ],
                    "luna-relay-sat-p00s00": [
                        NeighborAssignment("isl0", "earth-iss-sat-p00s00", "cross_plane_isl", 0)
                    ],
                },
                terminal_constraints={
                    "earth-iss-sat-p00s00": {"isl0": constraints},
                    "luna-relay-sat-p00s00": {"isl0": constraints},
                },
                body_frames=inputs.body_frames,
                polar_seam_enabled=False,
                latitude_threshold_deg=70.0,
            )
            return results[("earth-iss-sat-p00s00", "luna-relay-sat-p00s00")]

        result = _evaluate(0.25)
        assert result.feasible is True
        assert result.reject_reason == "ok"
        assert result.link_type == "cross_plane_isl"
        assert abs(result.range_km - expected_range_km) < 1e-3
        assert abs(result.orbital_one_way_ms - compute_latency_ms(result.range_km)) < 1e-9
        # Tracking enforcement was active, not skipped: the applied limit
        # is the terminal's, and squeezing it below the pair's actual
        # common-frame relative rate (~0.001 deg/s) flips the verdict.
        assert result.applied_max_tracking_rate_deg_s == 0.25
        squeezed = _evaluate(0.0005)
        assert squeezed.feasible is False
        assert squeezed.reject_reason == "tracking_exceeded"

        # The old aliasing put the ITRS vector here; prove the engine's
        # common state is GCRS-composed and far from the relabeled one.
        iss_state = states["earth-iss-sat-p00s00"]
        assert _distance(iss_state.position_common_km, iss_common_truth) < 1e-6
        itrs_relabeled = tuple(o + p for o, p in zip(earth_origin, boundary.position_itrs))
        assert _distance(iss_state.position_common_km, itrs_relabeled) > 12_000.0

        # Velocity flows from GCRS too: the relative angular rate computed
        # from common-frame state matches a hand composition.
        iss_vel_truth = tuple(
            o + v for o, v in zip(body_states["earth"].velocity_km_s, boundary.velocity_gcrs)
        )
        assert _distance(iss_state.velocity_common_km_s, iss_vel_truth) < 1e-9

    def test_snapshot_publishes_gcrs_composed_range(self):
        from ome.snapshot_builder import LinkSnapshotSource, build_link_state_snapshot

        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, body_states, inputs = self._states_at(probe_unix)
        pair = ("earth-iss-sat-p00s00", "luna-relay-sat-p00s00")

        source = LinkSnapshotSource(
            isl_state={pair: (True, True)},
            ground_state={},
            associations={},
            pending_teardowns={},
            propagated_states=states,
        )
        snapshot = build_link_state_snapshot(
            source,
            interface_map={pair: ("isl0", "isl0")},
            bandwidth_map={pair: 1000.0},
            sim_time=datetime.fromtimestamp(probe_unix, UTC),
            seq=1,
            interval_s=5.0,
        )
        link = next(entry for entry in snapshot.links if (entry.node_a, entry.node_b) == pair)
        expected_range = _distance(
            states[pair[0]].position_common_km, states[pair[1]].position_common_km
        )
        assert abs(link.range_km - expected_range) < 1e-3
        assert abs(link.latency_ms - compute_latency_ms(expected_range)) < 1e-3

    def test_earth_local_sgp4_is_unchanged(self):
        """Same-body geometry still consumes ITRS: the regression guard."""
        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, _, _ = self._states_at(probe_unix)
        iss = states["earth-iss-sat-p00s00"]
        assert _distance(iss.position_ecef_km, GOLDEN[43200.0]["itrs_pos"]) < POS_TOL_KM
        assert _distance(iss.velocity_ecef_km_s, GOLDEN[43200.0]["itrs_vel"]) < VEL_TOL_KMS


class TestPreviewRefusal:
    def test_mixed_body_diagnostic_pairs_are_refused(self):
        from nodalarc.models.addressing import NeighborAssignment
        from ome.coverage_preview import _scan_isl_failure_reasons

        inputs, _ = _earth_luna_world()
        earth_sat = next(s for s in inputs.satellites if s.central_body == "earth")
        luna_sat = next(s for s in inputs.satellites if s.central_body == "luna")
        neighbors = frozenset(
            {
                (
                    earth_sat.node_id,
                    NeighborAssignment("isl0", luna_sat.node_id, "isl", 0),
                ),
                (
                    luna_sat.node_id,
                    NeighborAssignment("isl0", earth_sat.node_id, "isl", 0),
                ),
            }
        )
        with pytest.raises(ValueError, match="mixed-body pair"):
            _scan_isl_failure_reasons(
                [earth_sat, luna_sat],
                inputs.addressing,
                neighbors,
                1_780_876_800.0,
                inputs.period,
                inputs.propagator_id,
                inputs.body_frames,
                {"polar_seam_enabled": False, "latitude_threshold_deg": 70.0},
            )


class TestEphemerisFrameGate:
    def test_gcrs_is_the_only_supported_kernel_frame(self):
        from nodalarc.runtime_support import FeatureCategory, RuntimeSupport

        support = RuntimeSupport.earth_luna()
        assert support.check_ephemeris_frame("gcrs") is None
        refusal = support.check_ephemeris_frame("icrf")
        assert refusal is not None
        assert refusal.category is FeatureCategory.EPHEMERIS_FRAME
        assert refusal.value == "icrf"
        assert "ephemeris kernel frame" in refusal.message

    def test_resolver_refuses_a_false_frame_label(self):
        from nodalarc.configuration_yaml import load_configuration_yaml
        from nodalarc.resolve_session import resolve_session
        from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

        root = Path(__file__).resolve().parents[2]
        document = load_configuration_yaml(
            (root / "catalog/nodalarc/sessions/earth-luna-dtn.yaml").read_bytes()
        )
        document["ephemeris"]["kernels"][0]["frame"] = "icrf"
        with pytest.raises(UnsupportedFeatureError) as excinfo:
            resolve_session(document)
        frame_refusals = [
            f
            for f in excinfo.value.features
            if f.category is FeatureCategory.EPHEMERIS_FRAME and f.value == "icrf"
        ]
        assert len(frame_refusals) == 1
        assert "ephemeris kernel frame" in frame_refusals[0].message
