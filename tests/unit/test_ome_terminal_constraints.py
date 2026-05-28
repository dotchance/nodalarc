"""OME terminal-role feasibility regressions."""

from __future__ import annotations

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment
from nodalarc.orbital import elements_from_params
from ome.event_stream import StepContext, compute_step
from ome.isl_engine import IslTerminalConstraints
from ome.propagation_engine import PropagatedState
from ome.propagator import EcefVec3, GeoPosition, Vec3


def test_cross_plane_isl_uses_cross_plane_tracking_limit(monkeypatch):
    """Cross-plane links must use cross-plane terminal physics, not isl[0].

    The crafted geometry has ~4.35 deg/s relative angular rate: below the
    legacy intra-plane 4.0-ish/global-ish permissive path would be easy to
    accidentally tune, but above the Iridium cross-plane 2.5 deg/s limit.
    The OME must reject it because the assigned interfaces are cross-plane
    terminals.
    """

    addressing = AddressingScheme()
    sat_a = SatelliteNode(
        0,
        0,
        elements_from_params(550.0, 86.4, 0.0, 0.0),
        isl_terminal_count=4,
        ground_terminal_count=0,
    )
    sat_b = SatelliteNode(
        1,
        0,
        elements_from_params(550.0, 86.4, 30.0, 0.0),
        isl_terminal_count=4,
        ground_terminal_count=0,
    )
    node_a = addressing.sat_id(0, 0)
    node_b = addressing.sat_id(1, 0)
    pair = (min(node_a, node_b), max(node_a, node_b))

    ctx = StepContext(
        satellites=[sat_a, sat_b],
        addressing=addressing,
        gs_positions={},
        gs_min_elevations={},
        gs_terminal_counts={},
        gs_policies={},
        gs_hysteresis={},
        gs_service_priorities={},
        simulation_fidelity="physical_v1",
        gs_terminal_profiles={},
        sat_ground_terminal_profiles={},
        gs_tenant_ids={},
        gs_reference_bodies={},
        ground_pair_terminal_types={},
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
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
    )

    pos_a = EcefVec3(Vec3(6921.0, 0.0, 0.0))
    vel_a = EcefVec3(Vec3(0.0, 7.59, 0.0))
    pos_b = EcefVec3(Vec3(7121.0, 0.0, 0.0))
    vel_b = EcefVec3(Vec3(0.0, -7.59, 0.0))

    def fake_propagation(*, satellites, addressing, epoch_unix, dt, propagator_id):
        del satellites, addressing, propagator_id
        sim_time_unix = epoch_unix + dt
        return {
            node_a: PropagatedState(
                node_id=node_a,
                sim_time_unix=sim_time_unix,
                position_ecef_km=pos_a,
                velocity_ecef_km_s=vel_a,
                geodetic=GeoPosition(0.0, 0.0, 550.0),
                propagator_id="test-fixture",
            ),
            node_b: PropagatedState(
                node_id=node_b,
                sim_time_unix=sim_time_unix,
                position_ecef_km=pos_b,
                velocity_ecef_km_s=vel_b,
                geodetic=GeoPosition(0.0, 0.0, 550.0),
                propagator_id="test-fixture",
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
