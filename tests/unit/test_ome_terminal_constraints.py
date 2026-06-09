"""OME terminal-role feasibility regressions."""

from __future__ import annotations

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment
from nodalarc.models.link_decisions import GroundPolicyAudit
from ome.event_stream import StepContext, compute_step
from ome.isl_engine import IslTerminalConstraints
from ome.propagation_engine import PropagatedState
from ome.propagator import EcefVec3, GeoPosition, Vec3

from tests.physics_fixtures import EARTH_TEST_BODY_FRAMES, earth_elements_from_params


def test_cross_plane_isl_uses_cross_plane_tracking_limit(monkeypatch):
    """Cross-plane links must use cross-plane terminal physics, not isl[0].

    The crafted geometry has ~4.35 deg/s relative angular rate: below the
    legacy intra-plane 4.0-ish/global-ish permissive path would be easy to
    accidentally tune, but above the Iridium cross-plane 2.5 deg/s limit.
    The OME must reject it because the assigned interfaces are cross-plane
    terminals.
    """

    addressing = AddressingScheme()
    node_a = "earth-iridium-sat-p00s00"
    node_b = "earth-iridium-sat-p01s00"
    sat_a = SatelliteNode(
        0,
        0,
        earth_elements_from_params(550.0, 86.4, 0.0, 0.0),
        node_id=node_a,
        central_body="earth",
        isl_terminal_count=4,
        ground_terminal_count=0,
    )
    sat_b = SatelliteNode(
        1,
        0,
        earth_elements_from_params(550.0, 86.4, 30.0, 0.0),
        node_id=node_b,
        central_body="earth",
        isl_terminal_count=4,
        ground_terminal_count=0,
    )
    pair = (min(node_a, node_b), max(node_a, node_b))

    ctx = StepContext(
        satellites=[sat_a, sat_b],
        addressing=addressing,
        gs_positions={},
        gs_min_elevations={},
        gs_terminal_counts={},
        gs_selection_policies={},
        gs_selection_policy_names={},
        gs_handover_policies={},
        gs_service_priorities={},
        ground_ranking_order=("service_priority", "selection_score", "lex_pair"),
        gs_handover_modes={},
        gs_mbb_overlap_ticks={},
        gs_mbb_reserve={},
        ground_mbb_preemption="off",
        ground_successor_abort_policy="hard_release",
        ground_cross_tenant_displacement="off",
        ground_bbm_acquire_timeout_ticks=1,
        ignored_ground_capacity_fields=(),
        ground_policy_audit=GroundPolicyAudit(
            selection_policies={},
            selection_policy_params={},
            handover_policies={},
            handover_policy_params={},
            ranking_order=("service_priority", "selection_score", "lex_pair"),
            handover_mode="bbm",
            handover_modes={},
            mbb_preemption="off",
            successor_abort_policy="hard_release",
            cross_tenant_displacement="off",
            mbb_overlap_ticks=3,
            mbb_overlap_ticks_by_gs={},
            mbb_reserve=0,
            mbb_reserve_by_gs={},
            bbm_acquire_timeout_ticks=1,
            ignored_capacity_fields=(),
        ),
        ground_link_model="terminal_physics",
        gs_terminal_profiles={},
        sat_ground_terminal_profiles={},
        sat_ground_terminal_indices_by_body={node_a: {}, node_b: {}},
        gs_tenant_ids={},
        gs_reference_bodies={},
        ground_candidate_satellites_by_gs={},
        ground_pair_terminal_types={},
        node_metadata={},
        by_node={
            node_a: [
                NeighborAssignment(
                    interface="isl2",
                    peer_node_id=node_b,
                    link_type="cross_plane_isl",
                    priority=2,
                )
            ],
            node_b: [
                NeighborAssignment(
                    interface="isl2",
                    peer_node_id=node_a,
                    link_type="cross_plane_isl",
                    priority=2,
                )
            ],
        },
        sat_isl_terminals={node_a: 4, node_b: 4},
        sat_isl_terminal_constraints={
            node_a: {
                "isl2": IslTerminalConstraints(
                    role="cross-plane",
                    max_range_km=4400.0,
                    max_tracking_rate_deg_s=2.5,
                    field_of_regard_deg=360.0,
                    terminal_type="rf",
                )
            },
            node_b: {
                "isl2": IslTerminalConstraints(
                    role="cross-plane",
                    max_range_km=4400.0,
                    max_tracking_rate_deg_s=2.5,
                    field_of_regard_deg=360.0,
                    terminal_type="rf",
                )
            },
        },
        sat_ground_terminals={node_a: 0, node_b: 0},
        propagator_id="keplerian-circular",
        body_frames=EARTH_TEST_BODY_FRAMES,
        active_bodies=frozenset({"earth"}),
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
    )

    pos_a = EcefVec3(Vec3(6921.0, 0.0, 0.0))
    vel_a = EcefVec3(Vec3(0.0, 7.59, 0.0))
    pos_b = EcefVec3(Vec3(7121.0, 0.0, 0.0))
    vel_b = EcefVec3(Vec3(0.0, -7.59, 0.0))

    def fake_propagation(
        *,
        satellites,
        addressing,
        epoch_unix,
        dt,
        propagator_id,
        body_states,
        body_frames,
    ):
        del satellites, addressing, propagator_id
        assert set(body_states) == {"earth"}
        assert set(body_frames) == {"earth"}
        sim_time_unix = epoch_unix + dt
        return {
            node_a: PropagatedState(
                node_id=node_a,
                sim_time_unix=sim_time_unix,
                position_ecef_km=pos_a,
                velocity_ecef_km_s=vel_a,
                geodetic=GeoPosition(0.0, 0.0, 550.0),
                propagator_id="test-fixture",
                central_body="earth",
            ),
            node_b: PropagatedState(
                node_id=node_b,
                sim_time_unix=sim_time_unix,
                position_ecef_km=pos_b,
                velocity_ecef_km_s=vel_b,
                geodetic=GeoPosition(0.0, 0.0, 550.0),
                propagator_id="test-fixture",
                central_body="earth",
            ),
        }

    monkeypatch.setattr("ome.event_stream.propagate_satellites", fake_propagation)

    isl_state = {pair: (True, True)}
    compute_step(
        ctx,
        epoch_unix=1735689600.0,
        step=0,
        step_seconds=1,
        timestamp_offset=0.0,
        isl_state=isl_state,
        gs_state={},
    )

    assert isl_state[pair] == (False, False)
