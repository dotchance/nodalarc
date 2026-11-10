# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Direct tests for the OME ISL feasibility and scheduling engine.

The engine decides each candidate from the tick's signals (both directions, light time,
occlusion by every body). Tests that reach a verdict build satellites, propagate them, and take
their signal endpoints with the functions compute_step uses, so the states the engine reads for
``range_km`` and the signals it decides from describe the same satellites. Tests that refuse
their inputs before any signal is read pass signals for no pair.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.frames import CommonVec3, EcefVec3, GeoPosition, Vec3
from nodalarc.models.addressing import NeighborAssignment
from nodalarc.ome_runtime import SatelliteNode
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext
from nodalarc.physics.context import tdb_seconds_of
from nodalarc.physics.lighttime import Endpoint, SiteEndpoint, signals_sent_at
from ome.direction_checks import IslLimits, PairSignals
from ome.isl_engine import (
    IslFeasibilityResult,
    IslTerminalConstraints,
    evaluate_isl_feasibility,
    isl_pair_reasons,
    schedule_isl_links,
)
from ome.propagation_engine import PropagatedState, propagate_satellites, satellite_endpoints

from tests.ome_runtime_fixtures import StaticOmeAddressing
from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, LUNA_TEST_BODY_FRAME, physics_for
from tests.seam_reference import DeclaredShell

TEST_BODY_FRAMES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
EPOCH = 1735689600.0
# Two evaluations of one signal in different batches agree within this (ms). The light-time
# iteration stops once a step moves by under 1 ns, and each step shrinks the error by the
# receiver's barycentric speed over c (about 1.3e-4), so evaluations agree within about
# 3e-13 s; measured 5e-17 s. The two directions of a pair differ by 132 ns or more here.
SAME_SIGNAL_MS = 1e-9


def _physics() -> PhysicsContext:
    return physics_for("earth", "luna")


def _no_signals() -> PairSignals:
    """Signals for no pair, for checks that refuse their inputs before reading a signal."""
    return PairSignals.sent_at(_physics(), TEST_BODY_FRAMES, {}, [], EPOCH)


def _satellite(
    node_id: str, elements: OrbitalElements, *, central_body: str = "earth"
) -> SatelliteNode:
    return SatelliteNode(
        plane=0,
        slot=0,
        elements=elements,
        elements_epoch_unix=EPOCH,
        node_id=node_id,
        central_body=central_body,
        isl_terminal_count=2,
        ground_terminal_count=0,
    )


def _propagated(
    satellites: list[SatelliteNode], propagator_id: str, sim_time_unix: float
) -> tuple[dict[str, PropagatedState], dict[str, Endpoint]]:
    """Each satellite's state at ``sim_time_unix`` and its signal endpoint, from the
    propagation and endpoint builders compute_step uses."""
    physics = _physics()
    addressing = StaticOmeAddressing(satellite_ids=tuple(sat.node_id for sat in satellites))
    states = propagate_satellites(
        satellites=satellites,
        addressing=addressing,
        epoch_unix=EPOCH,
        dt=sim_time_unix - EPOCH,
        propagator_id=propagator_id,
        body_frames=TEST_BODY_FRAMES,
        body_states=physics.body_states(TEST_BODY_FRAMES, sim_time_unix),
        physics=physics,
    )
    endpoints = satellite_endpoints(
        satellites=satellites,
        addressing=addressing,
        propagator_id=propagator_id,
        body_frames=TEST_BODY_FRAMES,
    )
    return states, endpoints


def _tick_signals(
    endpoints: dict[str, Endpoint],
    by_node: dict[str, list[NeighborAssignment]],
    sent_unix: float,
) -> PairSignals:
    """Both directions of every ISL candidate, ends sorted, as compute_step builds them."""
    pairs = sorted(
        {
            (min(node_id, assignment.peer_node_id), max(node_id, assignment.peer_node_id))
            for node_id, assignments in by_node.items()
            for assignment in assignments
        }
    )
    return PairSignals.sent_at(_physics(), TEST_BODY_FRAMES, endpoints, pairs, sent_unix)


def _state(
    node_id: str,
    position: Vec3,
    velocity: Vec3,
    *,
    central_body: str = "earth",
) -> PropagatedState:
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=1735689600.0,
        position_ecef_km=EcefVec3(position),
        velocity_ecef_km_s=EcefVec3(velocity),
        geodetic=GeoPosition(0.0, 0.0, 550.0),
        propagator_id="test-fixture",
        central_body=central_body,
        position_common_km=CommonVec3(*position),
        velocity_common_km_s=CommonVec3(*velocity),
        body_origin_common_km=CommonVec3(0.0, 0.0, 0.0),
    )


def _assignment(interface: str, peer: str, link_type: str, priority: int) -> NeighborAssignment:
    return NeighborAssignment(
        interface=interface,
        peer_node_id=peer,
        link_type=link_type,
        priority=priority,
        long_delay=False,
    )


def _constraints(
    role: str,
    tracking: float = 4.0,
    *,
    max_range_km: float = 4400.0,
) -> IslTerminalConstraints:
    return IslTerminalConstraints(
        role=role,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=tracking,
        field_of_regard_deg=360.0,
        terminal_type="rf",
    )


def test_isl_feasibility_fails_loud_when_node_state_missing():
    with pytest.raises(ValueError, match="missing propagated state.*sat-A"):
        evaluate_isl_feasibility(
            node_order=["sat-A", "sat-B"],
            sat_states={"sat-B": _state("sat-B", Vec3(7121.0, 0.0, 0.0), Vec3(0.0, 7.59, 0.0))},
            by_node={
                "sat-A": [_assignment("isl0", "sat-B", "intra_plane_isl", 1)],
                "sat-B": [_assignment("isl0", "sat-A", "intra_plane_isl", 1)],
            },
            terminal_constraints={
                "sat-A": {"isl0": _constraints("intra-plane")},
                "sat-B": {"isl0": _constraints("intra-plane")},
            },
            polar_seam_enabled=False,
            latitude_threshold_deg=70.0,
            body_frames=TEST_BODY_FRAMES,
            signals=_no_signals(),
        )


def test_isl_feasibility_fails_loud_when_peer_state_missing():
    with pytest.raises(ValueError, match="missing propagated state.*sat-B.*sat-A.*sat-B"):
        evaluate_isl_feasibility(
            node_order=["sat-A"],
            sat_states={"sat-A": _state("sat-A", Vec3(6921.0, 0.0, 0.0), Vec3(0.0, 7.59, 0.0))},
            by_node={
                "sat-A": [_assignment("isl0", "sat-B", "intra_plane_isl", 1)],
                "sat-B": [_assignment("isl0", "sat-A", "intra_plane_isl", 1)],
            },
            terminal_constraints={
                "sat-A": {"isl0": _constraints("intra-plane")},
                "sat-B": {"isl0": _constraints("intra-plane")},
            },
            polar_seam_enabled=False,
            latitude_threshold_deg=70.0,
            body_frames=TEST_BODY_FRAMES,
            signals=_no_signals(),
        )


def test_cross_plane_feasibility_applies_cross_plane_tracking_limit():
    """Counter-rotating satellites, prograde at 6921 km and retrograde at 7121 km, radially
    aligned at the tick: the line of sight turns at 4.3 deg/s, above the 2.5 deg/s limit."""
    states, endpoints = _propagated(
        [
            _satellite(
                "sat-A",
                OrbitalElements(
                    semi_major_axis_km=6921.0,
                    inclination_rad=0.0,
                    raan_rad=0.0,
                    mean_anomaly_rad=0.0,
                ),
            ),
            _satellite(
                "sat-B",
                OrbitalElements(
                    semi_major_axis_km=7121.0,
                    inclination_rad=math.pi,
                    raan_rad=0.0,
                    mean_anomaly_rad=0.0,
                ),
            ),
        ],
        "keplerian-circular",
        EPOCH,
    )
    by_node = {
        "sat-A": [_assignment("isl2", "sat-B", "cross_plane_isl", 2)],
        "sat-B": [_assignment("isl2", "sat-A", "cross_plane_isl", 2)],
    }
    feasibility = evaluate_isl_feasibility(
        node_order=["sat-A", "sat-B"],
        sat_states=states,
        by_node=by_node,
        terminal_constraints={
            "sat-A": {"isl2": _constraints("cross-plane", tracking=2.5)},
            "sat-B": {"isl2": _constraints("cross-plane", tracking=2.5)},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
        body_frames=TEST_BODY_FRAMES,
        signals=_tick_signals(endpoints, by_node, EPOCH),
    )

    result = feasibility[("sat-A", "sat-B")]
    assert not result.feasible
    assert result.reject_reason == "tracking_exceeded"
    assert result.applied_max_tracking_rate_deg_s == 2.5
    assert result.terminal_role_a == "cross-plane"
    assert result.terminal_role_b == "cross-plane"


def test_cross_plane_tracking_recovery_restores_feasibility():
    """Through the engine, the seam-crossing pass at the declared-parameter
    reference's samples: feasible, tracking_exceeded with the applied limit
    while range permits, feasible again. The satellites carry the reference's
    elements on the declared J2 mean-element model."""
    shell = DeclaredShell(780.0, 86.4, 31.6, 6, 15.0)

    def seam_satellite(node_id: str, plane: int, slot: int) -> SatelliteNode:
        semi_major_axis_km, inclination_rad, raan_rad, argument_of_latitude_rad = (
            shell.initial_elements(plane, slot)
        )
        return _satellite(
            node_id,
            OrbitalElements(
                semi_major_axis_km=semi_major_axis_km,
                inclination_rad=inclination_rad,
                raan_rad=raan_rad,
                mean_anomaly_rad=argument_of_latitude_rad,
            ),
        )

    satellites = [seam_satellite("sat-A", 5, 0), seam_satellite("sat-B", 0, 2)]
    by_node = {
        "sat-A": [_assignment("isl0", "sat-B", "cross_plane_isl", 1)],
        "sat-B": [_assignment("isl0", "sat-A", "cross_plane_isl", 1)],
    }
    for t_s, expected in ((2870, "ok"), (2890, "tracking_exceeded"), (2910, "ok")):
        states, endpoints = _propagated(satellites, "j2-mean-elements", EPOCH + t_s)
        feasibility = evaluate_isl_feasibility(
            node_order=["sat-A", "sat-B"],
            sat_states=states,
            by_node=by_node,
            terminal_constraints={
                "sat-A": {"isl0": _constraints("cross-plane", tracking=2.0, max_range_km=6000.0)},
                "sat-B": {"isl0": _constraints("cross-plane", tracking=2.0, max_range_km=6000.0)},
            },
            polar_seam_enabled=False,
            latitude_threshold_deg=70.0,
            body_frames=TEST_BODY_FRAMES,
            signals=_tick_signals(endpoints, by_node, EPOCH + t_s),
        )
        result = feasibility[("sat-A", "sat-B")]
        assert result.reject_reason == expected, (t_s, result)
        assert result.feasible == (expected == "ok")
        assert result.applied_max_tracking_rate_deg_s == 2.0
        assert result.range_km < 400.0


def _radially_aligned_pair() -> tuple[dict[str, PropagatedState], dict[str, Endpoint]]:
    """Two prograde equatorial satellites at 6921 km and 7121 km, 200 km apart on one radial
    line at the tick."""
    return _propagated(
        [
            _satellite(
                "sat-A",
                OrbitalElements(
                    semi_major_axis_km=6921.0,
                    inclination_rad=0.0,
                    raan_rad=0.0,
                    mean_anomaly_rad=0.0,
                ),
            ),
            _satellite(
                "sat-B",
                OrbitalElements(
                    semi_major_axis_km=7121.0,
                    inclination_rad=0.0,
                    raan_rad=0.0,
                    mean_anomaly_rad=0.0,
                ),
            ),
        ],
        "keplerian-circular",
        EPOCH,
    )


def test_terminal_role_mismatch_is_auditable_rejection():
    states, endpoints = _radially_aligned_pair()
    by_node = {
        "sat-A": [_assignment("isl0", "sat-B", "cross_plane_isl", 1)],
        "sat-B": [_assignment("isl0", "sat-A", "cross_plane_isl", 1)],
    }
    feasibility = evaluate_isl_feasibility(
        node_order=["sat-A", "sat-B"],
        sat_states=states,
        by_node=by_node,
        terminal_constraints={
            "sat-A": {"isl0": _constraints("intra-plane")},
            "sat-B": {"isl0": _constraints("cross-plane")},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
        body_frames=TEST_BODY_FRAMES,
        signals=_tick_signals(endpoints, by_node, EPOCH),
    )

    result = feasibility[("sat-A", "sat-B")]
    assert not result.feasible
    assert result.reject_reason == "terminal_role_mismatch"
    assert result.interface_a == "isl0"
    assert result.interface_b == "isl0"
    assert result.range_km > 0.0
    assert result.one_way_a_to_b_ms > 0.0
    assert result.one_way_b_to_a_ms > 0.0


def test_every_result_carries_each_directions_signal_light_time():
    """Every result, feasible or rejected for its terminals, carries as a_to_b the light time of
    the signal its pair's first node sends at the tick and as b_to_a the one its second node
    sends, each computed here directly by the signal layer. The two directions differ on every
    pair (the satellites move with Earth around the Sun during each flight; 133 ns on the
    radial pair), so a swap anywhere fails."""
    satellites = [
        _satellite(
            node_id,
            OrbitalElements(
                semi_major_axis_km=radius_km,
                inclination_rad=0.0,
                raan_rad=0.0,
                mean_anomaly_rad=math.radians(anomaly_deg),
            ),
        )
        for node_id, radius_km, anomaly_deg in (
            ("sat-A", 6921.0, 0.0),
            ("sat-B", 7121.0, 0.0),
            ("sat-C", 6921.0, 20.0),
        )
    ]
    states, endpoints = _propagated(satellites, "keplerian-circular", EPOCH)
    by_node = {
        "sat-A": [
            _assignment("isl0", "sat-B", "intra_plane_isl", 1),
            _assignment("isl1", "sat-C", "cross_plane_isl", 2),
        ],
        "sat-B": [
            _assignment("isl0", "sat-A", "intra_plane_isl", 1),
            _assignment("isl1", "sat-C", "intra_plane_isl", 1),
        ],
        "sat-C": [
            _assignment("isl0", "sat-A", "cross_plane_isl", 2),
            _assignment("isl1", "sat-B", "intra_plane_isl", 1),
        ],
    }
    optical = IslTerminalConstraints(
        role="intra-plane",
        max_range_km=4400.0,
        max_tracking_rate_deg_s=4.0,
        field_of_regard_deg=360.0,
        terminal_type="optical",
    )
    feasibility = evaluate_isl_feasibility(
        node_order=["sat-A", "sat-B", "sat-C"],
        sat_states=states,
        by_node=by_node,
        terminal_constraints={
            # sat-A's cross-plane link runs on an intra-plane terminal: a role mismatch.
            "sat-A": {"isl0": _constraints("intra-plane"), "isl1": _constraints("intra-plane")},
            "sat-B": {"isl0": _constraints("intra-plane"), "isl1": _constraints("intra-plane")},
            # sat-C's link to sat-B pairs an optical terminal with an RF one.
            "sat-C": {"isl0": _constraints("cross-plane"), "isl1": optical},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
        body_frames=TEST_BODY_FRAMES,
        signals=_tick_signals(endpoints, by_node, EPOCH),
    )

    assert {pair: result.reject_reason for pair, result in feasibility.items()} == {
        ("sat-A", "sat-B"): "ok",
        ("sat-A", "sat-C"): "terminal_role_mismatch",
        ("sat-B", "sat-C"): "terminal_type_mismatch",
    }
    for (node_a, node_b), result in feasibility.items():
        sent = signals_sent_at(
            _physics(),
            TEST_BODY_FRAMES,
            [endpoints[node_a], endpoints[node_b]],
            [endpoints[node_b], endpoints[node_a]],
            EPOCH,
        )
        a_to_b, b_to_a = (float(seconds) * 1000.0 for seconds in sent.light_time_s)
        assert abs(a_to_b - b_to_a) > 100 * SAME_SIGNAL_MS, (node_a, node_b)
        assert result.one_way_a_to_b_ms == pytest.approx(a_to_b, rel=0.0, abs=SAME_SIGNAL_MS)
        assert result.one_way_b_to_a_ms == pytest.approx(b_to_a, rel=0.0, abs=SAME_SIGNAL_MS)


def test_lunar_same_body_isl_uses_lunar_occluder_not_earth_default():
    """Two lunar satellites 2000 km up, 60 degrees apart in one plane: the path between them
    passes 3237 km from the Moon's centre, clear of the Moon (1738 km) and inside Earth's
    radius (6378 km), so only an Earth-sized occluder at the Moon would block it."""
    radius_km = LUNA_TEST_BODY_FRAME.equatorial_radius_km + 2000.0
    assert LUNA_TEST_BODY_FRAME.equatorial_radius_km < radius_km * math.cos(math.radians(30.0))
    assert radius_km * math.cos(math.radians(30.0)) < EARTH_TEST_BODY_FRAME.equatorial_radius_km

    def lunar_satellite(node_id: str, anomaly_deg: float) -> SatelliteNode:
        return _satellite(
            node_id,
            OrbitalElements(
                semi_major_axis_km=radius_km,
                inclination_rad=0.0,
                raan_rad=0.0,
                mean_anomaly_rad=math.radians(anomaly_deg),
            ),
            central_body="luna",
        )

    states, endpoints = _propagated(
        [lunar_satellite("luna-a", 0.0), lunar_satellite("luna-b", 60.0)], "two-body", EPOCH
    )
    by_node = {
        "luna-a": [_assignment("isl0", "luna-b", "intra_plane_isl", 1)],
        "luna-b": [_assignment("isl0", "luna-a", "intra_plane_isl", 1)],
    }
    feasibility = evaluate_isl_feasibility(
        node_order=["luna-a", "luna-b"],
        sat_states=states,
        by_node=by_node,
        terminal_constraints={
            "luna-a": {"isl0": _constraints("intra-plane", max_range_km=5000.0)},
            "luna-b": {"isl0": _constraints("intra-plane", max_range_km=5000.0)},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
        body_frames=TEST_BODY_FRAMES,
        signals=_tick_signals(endpoints, by_node, EPOCH),
    )

    result = feasibility[("luna-a", "luna-b")]
    assert result.feasible
    assert result.reject_reason == "ok"


# The Moon is within 0.01 degrees of Earth's equatorial plane at this instant, so a path from
# Earth toward the Moon can be laid across Earth's north pole.
MOON_ON_THE_EQUATOR = datetime(2026, 11, 19, 19, 30, tzinfo=UTC).timestamp()


def _limb_pair(physics: PhysicsContext, closest_km: np.ndarray) -> dict[str, Endpoint]:
    """An Earth endpoint and a lunar endpoint whose path runs parallel to the Earth-Moon line
    and passes ``closest_km`` (Earth-fixed, perpendicular to that line) from Earth's centre:
    the Earth end 8000 km before that point, the lunar end the same offset from the Moon's
    centre."""
    sent = np.array([tdb_seconds_of(MOON_ON_THE_EQUATOR)])
    earth_to_moon = (
        physics.motion.states("luna", sent)[0][0] - physics.motion.states("earth", sent)[0][0]
    )
    to_earth_fixed = physics.motion.orientations("earth", sent)[0][0]
    toward_moon = to_earth_fixed @ (earth_to_moon / np.linalg.norm(earth_to_moon))
    arrival = sent + np.linalg.norm(earth_to_moon) / SPEED_OF_LIGHT_KM_S
    to_moon_fixed = physics.motion.orientations("luna", arrival)[0][0]
    earth_end = closest_km - 8000.0 * toward_moon
    luna_end = to_moon_fixed @ (to_earth_fixed.T @ closest_km)
    return {
        "earth-sat": SiteEndpoint("earth", (earth_end[0], earth_end[1], earth_end[2])),
        "luna-sat": SiteEndpoint("luna", (luna_end[0], luna_end[1], luna_end[2])),
    }


def _limb_directions(physics: PhysicsContext) -> tuple[np.ndarray, np.ndarray]:
    """Unit directions, Earth-fixed and perpendicular to the Earth-Moon line: toward the
    north pole, and along the equator."""
    sent = np.array([tdb_seconds_of(MOON_ON_THE_EQUATOR)])
    earth_to_moon = (
        physics.motion.states("luna", sent)[0][0] - physics.motion.states("earth", sent)[0][0]
    )
    toward_moon = physics.motion.orientations("earth", sent)[0][0] @ earth_to_moon
    toward_moon /= np.linalg.norm(toward_moon)
    assert abs(toward_moon[2]) < 2e-4
    north = np.array([0.0, 0.0, 1.0])
    polar = north - (north @ toward_moon) * toward_moon
    equatorial = np.cross(north, toward_moon)
    return polar / np.linalg.norm(polar), equatorial / np.linalg.norm(equatorial)


def test_cross_body_occlusion_uses_the_oblate_ellipsoid_not_a_sphere():
    """A polar-limb path between an Earth endpoint and a lunar endpoint clearing Earth's
    ellipsoid by 8.2 km must not read as blocked by an equatorial-radius sphere (13.1 km of
    false penetration). Paths genuinely inside the ellipsoid, polar or equatorial, still block.
    Both directions of each path are signals sent at one instant; measured, the verdict flips
    within 0.013 km of the polar radius and within 0.75 km of the equatorial radius."""
    physics = _physics()
    polar, equatorial = _limb_directions(physics)
    pair = ("earth-sat", "luna-sat")
    limits = IslLimits(
        max_range_km=1.0e6,
        field_of_regard_deg=360.0,
        max_tracking_rate_deg_s=None,
        polar_seam=False,
    )

    def verdict(closest_km: np.ndarray) -> tuple[str, list[bool]]:
        signals = PairSignals.sent_at(
            physics,
            TEST_BODY_FRAMES,
            _limb_pair(physics, closest_km),
            [pair],
            MOON_ON_THE_EQUATOR,
        )
        (reason,) = isl_pair_reasons(
            signals=signals,
            body_frames=TEST_BODY_FRAMES,
            pairs=[pair],
            limits=[limits],
            latitude_threshold_deg=70.0,
        )
        earth_blocks = [
            bool(direction.occluded[0, direction.occulters.index("earth")])
            for direction in signals.in_order()
        ]
        return reason, earth_blocks

    # 6365 km: 8.248 km outside the polar radius, 13.137 km inside the
    # equatorial sphere the old test substituted.
    assert verdict(6365.0 * polar) == ("ok", [False, False])
    assert verdict(6350.0 * polar) == ("los_blocked", [True, True])
    assert verdict(6370.0 * equatorial) == ("los_blocked", [True, True])


def test_symmetric_isl_scheduling_respects_terminal_capacity_and_priority():
    feasible_ab = IslFeasibilityResult(
        pair=("sat-A", "sat-B"),
        link_type="intra_plane_isl",
        feasible=True,
        range_km=1000.0,
        one_way_a_to_b_ms=3.3356409519815204,
        one_way_b_to_a_ms=3.3356409519815204,
        reject_reason="ok",
        terminal_type="optical",
        terminal_role_a="intra-plane",
        terminal_role_b="intra-plane",
        interface_a="isl0",
        interface_b="isl0",
        applied_max_range_km=4400.0,
        applied_max_tracking_rate_deg_s=None,
        applied_field_of_regard_deg=360.0,
    )
    feasible_ac = IslFeasibilityResult(
        pair=("sat-A", "sat-C"),
        link_type="intra_plane_isl",
        feasible=True,
        range_km=1100.0,
        one_way_a_to_b_ms=3.6692050471796723,
        one_way_b_to_a_ms=3.6692050471796723,
        reject_reason="ok",
        terminal_type="optical",
        terminal_role_a="intra-plane",
        terminal_role_b="intra-plane",
        interface_a="isl1",
        interface_b="isl0",
        applied_max_range_km=4400.0,
        applied_max_tracking_rate_deg_s=None,
        applied_field_of_regard_deg=360.0,
    )

    scheduled = schedule_isl_links(
        feasibility={
            ("sat-A", "sat-B"): feasible_ab,
            ("sat-A", "sat-C"): feasible_ac,
        },
        by_node={
            "sat-A": [
                _assignment("isl0", "sat-B", "intra_plane_isl", 1),
                _assignment("isl1", "sat-C", "intra_plane_isl", 2),
            ],
            "sat-B": [_assignment("isl0", "sat-A", "intra_plane_isl", 1)],
            "sat-C": [_assignment("isl0", "sat-A", "intra_plane_isl", 1)],
        },
        terminal_counts={"sat-A": 1, "sat-B": 1, "sat-C": 1},
    )

    assert scheduled[("sat-A", "sat-B")].scheduled
    assert not scheduled[("sat-A", "sat-C")].scheduled
    assert scheduled[("sat-A", "sat-C")].unscheduled_reason == "capacity"


def test_isl_feasibility_is_topology_bounded_not_all_pairs():
    sat_count = 200
    node_ids = [f"sat-{idx:04d}" for idx in range(sat_count)]
    radius_km = 6921.0

    satellites = []
    by_node = {}
    terminal_constraints = {}
    for idx, node_id in enumerate(node_ids):
        satellites.append(
            _satellite(
                node_id,
                OrbitalElements(
                    semi_major_axis_km=radius_km,
                    inclination_rad=0.0,
                    raan_rad=0.0,
                    mean_anomaly_rad=2.0 * math.pi * idx / sat_count,
                ),
            )
        )
        prev_id = node_ids[(idx - 1) % sat_count]
        next_id = node_ids[(idx + 1) % sat_count]
        by_node[node_id] = [
            _assignment("isl0", prev_id, "intra_plane_isl", 1),
            _assignment("isl1", next_id, "intra_plane_isl", 1),
        ]
        terminal_constraints[node_id] = {
            "isl0": _constraints("intra-plane"),
            "isl1": _constraints("intra-plane"),
        }
    sat_states, endpoints = _propagated(satellites, "keplerian-circular", EPOCH)

    feasibility = evaluate_isl_feasibility(
        node_order=node_ids,
        sat_states=sat_states,
        by_node=by_node,
        terminal_constraints=terminal_constraints,
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
        body_frames=TEST_BODY_FRAMES,
        signals=_tick_signals(endpoints, by_node, EPOCH),
    )

    assert len(feasibility) == sat_count
    assert len(feasibility) < sat_count * (sat_count - 1) // 20
