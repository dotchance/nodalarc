# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""SGP4 common-frame authority: GCRS for cross-body, Earth-fixed for same-body.

The SGP4 boundary returns GCRS state only; the Earth-fixed state comes from
the shared physics module's Earth orientation. The common-frame slots of
PropagatedState are runtime-typed and refuse relabeled vectors. Goldens pin
the GCRS state at the ISS fixture epoch and twelve hours later, where the
inertial and Earth-fixed axes are nearly opposed and the old aliasing
produced a 12,709 km error, a false range, and a false los_blocked verdict.

There is no frontend SGP4 twin to cover: the sim worker propagates only
Keplerian/J2 elements and TLE rendering is driven by OME snapshot
geodetic positions, body-local and presentation-only.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from nodalarc.frames import CommonVec3, EcefVec3, Vec3
from nodalarc.propagator import propagate_sgp4_tle_gcrs

from tests.catalog_session_fixtures import shipped_read_view

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
ISS_TLE_EPOCH_UNIX = 1615896900.000275

# Goldens from the Skyfield GCRS boundary, whose conversion the published
# references in tests/unit/physics/test_tle_reference.py qualify. Skyfield and
# sgp4 are lockfile-pinned; a dependency upgrade that moves these numbers must
# arrive as a deliberate diff of this table, not be absorbed by tolerance.
GOLDEN = {
    0.0: {
        "gcrs_pos": (-4231.119851995798, 2377.5438181736145, 4748.974672117138),
        "gcrs_vel": (-5.5845138643634815, -4.489569376632045, -2.719985233454282),
    },
    43200.0: {
        "gcrs_pos": (5126.795151947043, 3759.0822575731286, 2399.3701354879504),
        "gcrs_vel": (-4.6437071007668305, 2.890797125620803, 5.366572005142366),
    },
}

POS_TOL_KM = 1e-6
VEL_TOL_KMS = 1e-9


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class TestBoundaryGoldens:
    """The GCRS boundary, pinned at both instants."""

    @pytest.mark.parametrize("dt_s", [0.0, 43200.0])
    def test_gcrs_state_matches_goldens(self, dt_s):
        position, velocity = propagate_sgp4_tle_gcrs(
            ISS_TLE_LINE_1, ISS_TLE_LINE_2, ISS_TLE_EPOCH_UNIX + dt_s
        )
        golden = GOLDEN[dt_s]
        assert _distance(position, golden["gcrs_pos"]) < POS_TOL_KM
        assert _distance(velocity, golden["gcrs_vel"]) < VEL_TOL_KMS


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

        position, velocity = propagate_sgp4_tle_gcrs(
            ISS_TLE_LINE_1, ISS_TLE_LINE_2, ISS_TLE_EPOCH_UNIX
        )
        assert isinstance(position, GcrsVec3)
        assert isinstance(velocity, GcrsVec3)


def _earth_luna_world():
    """Real resolved earth-luna inputs plus the resolved session."""
    from nodalarc.configuration_yaml import load_configuration_yaml
    from nodalarc.ome_inputs import build_ome_inputs_from_resolved
    from nodalarc.resolve_session import resolve_session

    root = Path(__file__).resolve().parents[3]
    document = load_configuration_yaml(
        (root / "catalog/nodalarc/sessions/earth-luna-dtn.yaml").read_bytes()
    )
    resolved = resolve_session(document, catalog=shipped_read_view())
    return build_ome_inputs_from_resolved(resolved), resolved


class TestCrossBodyAuthority:
    """A real SGP4 Earth endpoint against a de440s lunar endpoint."""

    def _satellites_at(self, probe_unix, inputs):
        """The SGP4 ISS and a two-body lunar relay anchored at ``probe_unix``."""
        from nodalarc.ome_runtime import SatelliteNode, retarget_satellites
        from nodalarc.orbital import OrbitalElements

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
        return [iss, relay]

    def _states_at(self, probe_unix):
        from ome.propagation_engine import propagate_satellites

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        inputs, resolved = _earth_luna_world()
        body_states = inputs.physics.body_states({"earth", "luna"}, probe_unix)
        states = propagate_satellites(
            satellites=self._satellites_at(probe_unix, inputs),
            addressing=StaticOmeAddressing(),
            epoch_unix=probe_unix,
            dt=0.0,
            propagator_id="mixed",
            body_frames=inputs.body_frames,
            body_states=body_states,
            physics=inputs.physics,
        )
        return states, body_states, inputs

    def _signals_at(self, probe_unix, inputs):
        """Both directions of the ISS-relay pair, sent at ``probe_unix`` from the endpoints of
        the same satellites ``_states_at`` propagates."""
        from ome.direction_checks import PairSignals
        from ome.propagation_engine import satellite_endpoints

        from tests.ome_runtime_fixtures import StaticOmeAddressing

        return PairSignals.sent_at(
            inputs.physics,
            inputs.body_frames,
            satellite_endpoints(
                satellites=self._satellites_at(probe_unix, inputs),
                addressing=StaticOmeAddressing(),
                propagator_id="mixed",
                body_frames=inputs.body_frames,
            ),
            [("earth-iss-sat-p00s00", "luna-relay-sat-p00s00")],
            probe_unix,
        )

    def test_cross_body_range_latency_and_verdict(self):
        from nodalarc.models.addressing import NeighborAssignment
        from ome.isl_engine import IslTerminalConstraints, evaluate_isl_feasibility

        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, body_states, inputs = self._states_at(probe_unix)
        signals = self._signals_at(probe_unix, inputs)

        # Independent truth: boundary GCRS plus ephemeris origins, composed
        # by hand, never through the engine or the ISL machinery.
        boundary_position, boundary_velocity = propagate_sgp4_tle_gcrs(
            ISS_TLE_LINE_1, ISS_TLE_LINE_2, probe_unix
        )
        earth_origin = body_states["earth"].position_km
        iss_common_truth = tuple(o + p for o, p in zip(earth_origin, boundary_position))
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
                        NeighborAssignment(
                            "isl0", "luna-relay-sat-p00s00", "cross_plane_isl", 0, False
                        )
                    ],
                    "luna-relay-sat-p00s00": [
                        NeighborAssignment(
                            "isl0", "earth-iss-sat-p00s00", "cross_plane_isl", 0, False
                        )
                    ],
                },
                terminal_constraints={
                    "earth-iss-sat-p00s00": {"isl0": constraints},
                    "luna-relay-sat-p00s00": {"isl0": constraints},
                },
                body_frames=inputs.body_frames,
                polar_seam_enabled=False,
                latitude_threshold_deg=70.0,
                signals=signals,
            )
            return results[("earth-iss-sat-p00s00", "luna-relay-sat-p00s00")]

        result = _evaluate(0.25)
        assert result.feasible is True
        assert result.reject_reason == "ok"
        assert result.link_type == "cross_plane_isl"
        assert abs(result.range_km - expected_range_km) < 1e-3
        # Each direction's delay is the light time of the signal that end sends at the probe.
        iss_sends, relay_sends = signals.in_order()
        assert result.one_way_a_to_b_ms == float(iss_sends.light_time_s[0]) * 1000.0
        assert result.one_way_b_to_a_ms == float(relay_sends.light_time_s[0]) * 1000.0
        assert result.one_way_a_to_b_ms != result.one_way_b_to_a_ms
        # Tracking enforcement was active, not skipped: the applied limit
        # is the terminal's, and squeezing it below the pair's line-of-sight
        # rate (0.0010 deg/s, inertial because the ends orbit different
        # bodies) flips the verdict.
        assert result.applied_max_tracking_rate_deg_s == 0.25
        squeezed = _evaluate(0.0005)
        assert squeezed.feasible is False
        assert squeezed.reject_reason == "tracking_exceeded"

        # The old aliasing put the Earth-fixed vector here; prove the engine's
        # common state is GCRS-composed and far from the relabeled one.
        iss_state = states["earth-iss-sat-p00s00"]
        assert _distance(iss_state.position_common_km, iss_common_truth) < 1e-6
        fixed_relabeled = tuple(o + p for o, p in zip(earth_origin, iss_state.position_ecef_km))
        assert _distance(iss_state.position_common_km, fixed_relabeled) > 12_000.0

        # Velocity flows from GCRS too: the relative angular rate computed
        # from common-frame state matches a hand composition.
        iss_vel_truth = tuple(
            o + v for o, v in zip(body_states["earth"].velocity_km_s, boundary_velocity)
        )
        assert _distance(iss_state.velocity_common_km_s, iss_vel_truth) < 1e-9

    def test_an_inter_body_isl_range_is_the_gcrs_composed_distance(self):
        """The ISL engine measures a pair whose ends orbit two bodies (the ISS and the lunar
        relay) between their common-frame positions, which compose SGP4's GCRS state with
        Earth's barycentric position."""
        from nodalarc.geo import compute_range_km
        from ome.isl_engine import _state_vectors

        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, _body_states, _inputs = self._states_at(probe_unix)
        iss, relay = states["earth-iss-sat-p00s00"], states["luna-relay-sat-p00s00"]
        position_a, _velocity_a, position_b, _velocity_b = _state_vectors(iss, relay)

        assert (
            abs(
                compute_range_km(position_a, position_b)
                - _distance(iss.position_common_km, relay.position_common_km)
            )
            < 1e-9
        )

    def test_earth_fixed_state_is_the_inertial_state_in_itrf93(self):
        """Same-body geometry consumes the GCRS state in NAIF's ITRF93 frame.

        TLE satellites, analytic satellites and Earth sites take Earth
        orientation from the one physics context.
        """
        probe_unix = ISS_TLE_EPOCH_UNIX + 43200.0
        states, _, inputs = self._states_at(probe_unix)
        iss = states["earth-iss-sat-p00s00"]
        rotation = inputs.physics.orientation("earth", probe_unix).rotation
        gcrs_position, _velocity = propagate_sgp4_tle_gcrs(
            ISS_TLE_LINE_1, ISS_TLE_LINE_2, probe_unix
        )
        assert _distance(iss.position_ecef_km, rotation @ np.array(gcrs_position)) < POS_TOL_KM
