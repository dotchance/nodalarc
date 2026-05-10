# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""ISL feasibility and scheduling engine.

The engine consumes propagated satellite states plus terminal assignments and
returns auditable link decisions. It is intentionally terminal-role-aware:
candidate links are evaluated against the physical terminals assigned to that
interface pair, never a global constellation default.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.geo import compute_latency_ms
from nodalarc.models.addressing import NeighborAssignment

from ome.propagation_engine import PropagatedState
from ome.visibility import (
    check_isl_visibility,
    compute_range,
    enforce_symmetric_scheduling,
    schedule_isl_terminals,
)


@dataclass(frozen=True)
class IslTerminalConstraints:
    """Physical constraints applied to one endpoint of an ISL terminal.

    Role is intentionally a stable string from the terminal model, not a
    terminal-list index. The OME uses this object to keep structural topology
    (`intra_plane_isl` vs `cross_plane_isl`) tied to the hardware limits that
    real terminals impose.
    """

    role: str | None
    max_range_km: float
    max_tracking_rate_deg_s: float
    field_of_regard_deg: float
    terminal_type: str


@dataclass(frozen=True)
class IslFeasibilityResult:
    """Auditable physical feasibility result for one ISL candidate pair."""

    pair: tuple[str, str]
    link_type: str
    feasible: bool
    range_km: float
    orbital_one_way_ms: float
    reject_reason: str
    terminal_role_a: str | None
    terminal_role_b: str | None
    terminal_type: str
    interface_a: str
    interface_b: str
    applied_max_range_km: float
    applied_max_tracking_rate_deg_s: float | None
    applied_field_of_regard_deg: float


@dataclass(frozen=True)
class ScheduledIsl:
    """ISL scheduling result after symmetric terminal-capacity allocation."""

    pair: tuple[str, str]
    terminal_role_a: str | None
    terminal_role_b: str | None
    range_km: float
    orbital_one_way_ms: float
    scheduled: bool
    unscheduled_reason: str | None


def _role_allows_link(role: str | None, link_type: str) -> bool:
    """Return whether a terminal role can serve a structural ISL type."""
    if role is None:
        return True
    if link_type == "intra_plane_isl":
        return role == "intra-plane"
    if link_type == "cross_plane_isl":
        return role == "cross-plane"
    return True


def evaluate_isl_feasibility(
    *,
    node_order: list[str],
    sat_states: Mapping[str, PropagatedState],
    by_node: Mapping[str, list[NeighborAssignment]],
    terminal_constraints: Mapping[str, Mapping[str, IslTerminalConstraints]],
    polar_seam_enabled: bool,
    latitude_threshold_deg: float,
) -> dict[tuple[str, str], IslFeasibilityResult]:
    """Evaluate physical ISL feasibility for assigned neighbor pairs."""
    results: dict[tuple[str, str], IslFeasibilityResult] = {}

    for node_id in node_order:
        state_a = sat_states.get(node_id)
        if state_a is None:
            continue

        for assignment_a in by_node.get(node_id, []):
            peer_id = assignment_a.peer_node_id
            state_b = sat_states.get(peer_id)
            if state_b is None:
                continue

            pair = (min(node_id, peer_id), max(node_id, peer_id))
            if pair[0] != node_id:
                continue

            assignment_b = next(
                (
                    peer_assignment
                    for peer_assignment in by_node.get(peer_id, [])
                    if peer_assignment.peer_node_id == node_id
                ),
                None,
            )
            if assignment_b is None:
                raise ValueError(
                    f"Missing reciprocal ISL assignment for {node_id}<->{peer_id}; "
                    "terminal-aware feasibility requires both endpoint interfaces"
                )

            constraints_a = terminal_constraints.get(node_id, {}).get(assignment_a.interface)
            constraints_b = terminal_constraints.get(peer_id, {}).get(assignment_b.interface)
            if constraints_a is None or constraints_b is None:
                raise ValueError(
                    f"Missing terminal constraints for {node_id}:{assignment_a.interface}<->"
                    f"{peer_id}:{assignment_b.interface}"
                )

            max_range_km = min(constraints_a.max_range_km, constraints_b.max_range_km)
            max_tracking_rate_deg_s = min(
                constraints_a.max_tracking_rate_deg_s,
                constraints_b.max_tracking_rate_deg_s,
            )
            field_of_regard_deg = min(
                constraints_a.field_of_regard_deg,
                constraints_b.field_of_regard_deg,
            )
            applied_tracking_rate = (
                max_tracking_rate_deg_s if assignment_a.link_type == "cross_plane_isl" else None
            )
            terminal_type = constraints_a.terminal_type

            if constraints_a.terminal_type != constraints_b.terminal_type:
                range_km = compute_range(state_a.position_ecef_km, state_b.position_ecef_km)
                results[pair] = IslFeasibilityResult(
                    pair=pair,
                    link_type=assignment_a.link_type,
                    feasible=False,
                    range_km=range_km,
                    orbital_one_way_ms=compute_latency_ms(range_km),
                    reject_reason="terminal_type_mismatch",
                    terminal_role_a=constraints_a.role,
                    terminal_role_b=constraints_b.role,
                    terminal_type=f"{constraints_a.terminal_type}/{constraints_b.terminal_type}",
                    interface_a=assignment_a.interface,
                    interface_b=assignment_b.interface,
                    applied_max_range_km=max_range_km,
                    applied_max_tracking_rate_deg_s=applied_tracking_rate,
                    applied_field_of_regard_deg=field_of_regard_deg,
                )
                continue

            if not _role_allows_link(
                constraints_a.role, assignment_a.link_type
            ) or not _role_allows_link(constraints_b.role, assignment_a.link_type):
                range_km = compute_range(state_a.position_ecef_km, state_b.position_ecef_km)
                results[pair] = IslFeasibilityResult(
                    pair=pair,
                    link_type=assignment_a.link_type,
                    feasible=False,
                    range_km=range_km,
                    orbital_one_way_ms=compute_latency_ms(range_km),
                    reject_reason="terminal_role_mismatch",
                    terminal_role_a=constraints_a.role,
                    terminal_role_b=constraints_b.role,
                    terminal_type=terminal_type,
                    interface_a=assignment_a.interface,
                    interface_b=assignment_b.interface,
                    applied_max_range_km=max_range_km,
                    applied_max_tracking_rate_deg_s=applied_tracking_rate,
                    applied_field_of_regard_deg=field_of_regard_deg,
                )
                continue

            visibility = check_isl_visibility(
                state_a.position_ecef_km,
                state_a.velocity_ecef_km_s,
                state_b.position_ecef_km,
                state_b.velocity_ecef_km_s,
                max_range_km=max_range_km,
                max_tracking_rate_deg_s=applied_tracking_rate,
                field_of_regard_deg=field_of_regard_deg,
                polar_seam_enabled=polar_seam_enabled
                and assignment_a.link_type == "cross_plane_isl",
                latitude_threshold_deg=latitude_threshold_deg,
                geo_a=state_a.geodetic,
                geo_b=state_b.geodetic,
            )

            results[pair] = IslFeasibilityResult(
                pair=pair,
                link_type=assignment_a.link_type,
                feasible=visibility.visible,
                range_km=visibility.range_km,
                orbital_one_way_ms=compute_latency_ms(visibility.range_km),
                reject_reason=visibility.reason,
                terminal_role_a=constraints_a.role,
                terminal_role_b=constraints_b.role,
                terminal_type=terminal_type,
                interface_a=assignment_a.interface,
                interface_b=assignment_b.interface,
                applied_max_range_km=max_range_km,
                applied_max_tracking_rate_deg_s=applied_tracking_rate,
                applied_field_of_regard_deg=field_of_regard_deg,
            )

    return results


def schedule_isl_links(
    *,
    feasibility: Mapping[tuple[str, str], IslFeasibilityResult],
    by_node: Mapping[str, list[NeighborAssignment]],
    terminal_counts: Mapping[str, int],
) -> dict[tuple[str, str], ScheduledIsl]:
    """Allocate ISL terminals symmetrically for feasible links."""
    node_feasible_isls: dict[str, list[tuple[str, int, float]]] = {}
    for pair, result in feasibility.items():
        if not result.feasible:
            continue
        node_a, node_b = pair
        for assignment in by_node.get(node_a, []):
            if assignment.peer_node_id == node_b:
                node_feasible_isls.setdefault(node_a, []).append(
                    (node_b, assignment.priority, result.range_km),
                )
                break
        for assignment in by_node.get(node_b, []):
            if assignment.peer_node_id == node_a:
                node_feasible_isls.setdefault(node_b, []).append(
                    (node_a, assignment.priority, result.range_km),
                )
                break

    all_isl_schedules: dict[str, list] = {}
    for node_id, feasible_links in node_feasible_isls.items():
        terminal_count = terminal_counts[node_id]
        all_isl_schedules[node_id] = schedule_isl_terminals(node_id, feasible_links, terminal_count)

    all_isl_schedules = enforce_symmetric_scheduling(all_isl_schedules)

    scheduled_by_pair: dict[tuple[str, str], bool] = {}
    for _node_id, links in all_isl_schedules.items():
        for link in links:
            pair = (min(link.node_a, link.node_b), max(link.node_a, link.node_b))
            if pair not in scheduled_by_pair:
                scheduled_by_pair[pair] = link.scheduled
            else:
                scheduled_by_pair[pair] = scheduled_by_pair[pair] and link.scheduled

    scheduled: dict[tuple[str, str], ScheduledIsl] = {}
    for pair, result in feasibility.items():
        is_scheduled = scheduled_by_pair.get(pair, False) if result.feasible else False
        if is_scheduled:
            reason = None
        elif result.feasible:
            reason = "capacity"
        else:
            reason = result.reject_reason
        scheduled[pair] = ScheduledIsl(
            pair=pair,
            terminal_role_a=result.terminal_role_a,
            terminal_role_b=result.terminal_role_b,
            range_km=result.range_km,
            orbital_one_way_ms=result.orbital_one_way_ms,
            scheduled=is_scheduled,
            unscheduled_reason=reason,
        )

    return scheduled
