# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Direct tests for the OME ISL feasibility and scheduling engine."""

from __future__ import annotations

import math

import pytest
from nodalarc.models.addressing import NeighborAssignment
from ome.isl_engine import (
    IslFeasibilityResult,
    IslTerminalConstraints,
    evaluate_isl_feasibility,
    schedule_isl_links,
)
from ome.propagation_engine import PropagatedState
from ome.propagator import EcefVec3, GeoPosition, Vec3


def _state(node_id: str, position: Vec3, velocity: Vec3) -> PropagatedState:
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=1735689600.0,
        position_ecef_km=EcefVec3(position),
        velocity_ecef_km_s=EcefVec3(velocity),
        geodetic=GeoPosition(0.0, 0.0, 550.0),
        propagator_id="test-fixture",
    )


def _assignment(interface: str, peer: str, link_type: str, priority: int) -> NeighborAssignment:
    return NeighborAssignment(
        interface=interface,
        peer_node_id=peer,
        link_type=link_type,
        priority=priority,
    )


def _constraints(role: str, tracking: float = 4.0) -> IslTerminalConstraints:
    return IslTerminalConstraints(
        role=role,
        max_range_km=4400.0,
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
        )


def test_cross_plane_feasibility_applies_cross_plane_tracking_limit():
    feasibility = evaluate_isl_feasibility(
        node_order=["sat-A", "sat-B"],
        sat_states={
            "sat-A": _state("sat-A", Vec3(6921.0, 0.0, 0.0), Vec3(0.0, 7.59, 0.0)),
            "sat-B": _state("sat-B", Vec3(7121.0, 0.0, 0.0), Vec3(0.0, -7.59, 0.0)),
        },
        by_node={
            "sat-A": [_assignment("isl2", "sat-B", "cross_plane_isl", 2)],
            "sat-B": [_assignment("isl2", "sat-A", "cross_plane_isl", 2)],
        },
        terminal_constraints={
            "sat-A": {"isl2": _constraints("cross-plane", tracking=2.5)},
            "sat-B": {"isl2": _constraints("cross-plane", tracking=2.5)},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
    )

    result = feasibility[("sat-A", "sat-B")]
    assert not result.feasible
    assert result.reject_reason == "tracking_exceeded"
    assert result.applied_max_tracking_rate_deg_s == 2.5
    assert result.terminal_role_a == "cross-plane"
    assert result.terminal_role_b == "cross-plane"


def test_terminal_role_mismatch_is_auditable_rejection():
    feasibility = evaluate_isl_feasibility(
        node_order=["sat-A", "sat-B"],
        sat_states={
            "sat-A": _state("sat-A", Vec3(6921.0, 0.0, 0.0), Vec3(0.0, 7.59, 0.0)),
            "sat-B": _state("sat-B", Vec3(7121.0, 0.0, 0.0), Vec3(0.0, 7.59, 0.0)),
        },
        by_node={
            "sat-A": [_assignment("isl0", "sat-B", "cross_plane_isl", 1)],
            "sat-B": [_assignment("isl0", "sat-A", "cross_plane_isl", 1)],
        },
        terminal_constraints={
            "sat-A": {"isl0": _constraints("intra-plane")},
            "sat-B": {"isl0": _constraints("cross-plane")},
        },
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
    )

    result = feasibility[("sat-A", "sat-B")]
    assert not result.feasible
    assert result.reject_reason == "terminal_role_mismatch"
    assert result.interface_a == "isl0"
    assert result.interface_b == "isl0"
    assert result.range_km > 0.0
    assert result.orbital_one_way_ms > 0.0


def test_symmetric_isl_scheduling_respects_terminal_capacity_and_priority():
    feasible_ab = IslFeasibilityResult(
        pair=("sat-A", "sat-B"),
        link_type="intra_plane_isl",
        feasible=True,
        range_km=1000.0,
        orbital_one_way_ms=3.3356409519815204,
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
        orbital_one_way_ms=3.6692050471796723,
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

    sat_states = {}
    by_node = {}
    terminal_constraints = {}
    for idx, node_id in enumerate(node_ids):
        theta = 2.0 * math.pi * idx / sat_count
        sat_states[node_id] = _state(
            node_id,
            Vec3(radius_km * math.cos(theta), radius_km * math.sin(theta), 0.0),
            Vec3(-7.5 * math.sin(theta), 7.5 * math.cos(theta), 0.0),
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

    feasibility = evaluate_isl_feasibility(
        node_order=node_ids,
        sat_states=sat_states,
        by_node=by_node,
        terminal_constraints=terminal_constraints,
        polar_seam_enabled=False,
        latitude_threshold_deg=70.0,
    )

    assert len(feasibility) == sat_count
    assert len(feasibility) < sat_count * (sat_count - 1) // 20
