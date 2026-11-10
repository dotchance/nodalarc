# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""ISL feasibility and scheduling engine.

The engine consumes propagated satellite states plus terminal assignments and
returns auditable link decisions. It is intentionally terminal-role-aware:
candidate links are evaluated against the physical terminals assigned to that
interface pair, never a global constellation default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import CommonVec3, Vec3
from nodalarc.geo import compute_range_km
from nodalarc.models.addressing import NeighborAssignment
from nodalarc.physics.geodesy import geodetic_from_body_fixed_batch
from nodalarc.physics.lighttime import SignalEnd

from ome.direction_checks import DirectionEnds, IslLimits, PairSignals, isl_reasons
from ome.propagation_engine import PropagatedState
from ome.visibility import enforce_symmetric_scheduling, schedule_isl_terminals


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
    one_way_a_to_b_ms: float
    one_way_b_to_a_ms: float
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
    one_way_a_to_b_ms: float
    one_way_b_to_a_ms: float
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


def _state_vectors(
    state_a: PropagatedState,
    state_b: PropagatedState,
) -> tuple[Vec3 | CommonVec3, Vec3 | CommonVec3, Vec3 | CommonVec3, Vec3 | CommonVec3]:
    """Return position/velocity vectors in the frame appropriate for this pair."""
    if state_a.central_body == state_b.central_body:
        return (
            state_a.position_ecef_km,
            state_a.velocity_ecef_km_s,
            state_b.position_ecef_km,
            state_b.velocity_ecef_km_s,
        )
    return (
        state_a.position_common_km,
        state_a.velocity_common_km_s,
        state_b.position_common_km,
        state_b.velocity_common_km_s,
    )


def _latitudes_deg(end: SignalEnd, body_frames: Mapping[str, BodyFrame]) -> np.ndarray:
    """Geodetic latitude of each end at its own epoch, on its central body."""
    latitudes = np.empty(len(end.body_ids))
    for body_id in set(end.body_ids):
        rows = end.body_ids == body_id
        latitudes[rows], _lon, _alt = geodetic_from_body_fixed_batch(
            end.fixed_km[rows], body_frames[body_id]
        )
    return latitudes


@dataclass(frozen=True)
class _Candidate:
    pair: tuple[str, str]
    link_type: str
    constraints_a: IslTerminalConstraints
    constraints_b: IslTerminalConstraints
    interface_a: str
    interface_b: str
    range_km: float
    limits: IslLimits
    applied_tracking_rate: float | None


def _result(
    *,
    pair: tuple[str, str],
    link_type: str,
    constraints_a: IslTerminalConstraints,
    constraints_b: IslTerminalConstraints,
    interface_a: str,
    interface_b: str,
    range_km: float,
    one_way_ms: tuple[float, float],
    reason: str,
    terminal_type: str,
    max_range_km: float,
    applied_tracking_rate: float | None,
    field_of_regard_deg: float,
) -> IslFeasibilityResult:
    return IslFeasibilityResult(
        pair=pair,
        link_type=link_type,
        feasible=reason == "ok",
        range_km=range_km,
        one_way_a_to_b_ms=one_way_ms[0],
        one_way_b_to_a_ms=one_way_ms[1],
        reject_reason=reason,
        terminal_role_a=constraints_a.role,
        terminal_role_b=constraints_b.role,
        terminal_type=terminal_type,
        interface_a=interface_a,
        interface_b=interface_b,
        applied_max_range_km=max_range_km,
        applied_max_tracking_rate_deg_s=applied_tracking_rate,
        applied_field_of_regard_deg=field_of_regard_deg,
    )


def isl_pair_reasons(
    *,
    signals: PairSignals,
    body_frames: Mapping[str, BodyFrame],
    pairs: Sequence[tuple[str, str]],
    limits: Sequence[IslLimits],
    latitude_threshold_deg: float,
) -> list[str]:
    """Per ISL candidate, "ok" or the first failing constraint of its two directions (a->b
    before b->a)."""
    if not pairs:
        return []
    forward, backward = signals.directions(pairs)
    reasons = []
    for direction in (forward, backward):
        reasons.append(
            isl_reasons(
                DirectionEnds.from_signals(direction),
                limits,
                transmitter_latitude_deg=_latitudes_deg(direction.transmitter, body_frames),
                receiver_latitude_deg=_latitudes_deg(direction.receiver, body_frames),
                latitude_threshold_deg=latitude_threshold_deg,
            )
        )
    return [
        str(ahead) if ahead != "ok" else str(back)
        for ahead, back in zip(reasons[0], reasons[1], strict=True)
    ]


def evaluate_isl_feasibility(
    *,
    node_order: list[str],
    sat_states: Mapping[str, PropagatedState],
    by_node: Mapping[str, list[NeighborAssignment]],
    terminal_constraints: Mapping[str, Mapping[str, IslTerminalConstraints]],
    body_frames: Mapping[str, BodyFrame],
    polar_seam_enabled: bool,
    latitude_threshold_deg: float,
    signals: PairSignals,
) -> dict[tuple[str, str], IslFeasibilityResult]:
    """Evaluate physical ISL feasibility for assigned neighbor pairs.

    Each candidate's two directions are the tick's signals: the transmitter's constraints at
    the tick, the receiver's at arrival, each along its apparent
    direction, and the path against every session body and the Sun. A link is feasible while
    both directions are; the reason is the first failure, a->b before b->a. ``range_km`` is the
    ends' geometric separation at the tick.
    """
    results: dict[tuple[str, str], IslFeasibilityResult] = {}
    candidates: list[_Candidate] = []
    rejected: list[tuple[dict, str, str]] = []

    for node_id in node_order:
        state_a = sat_states.get(node_id)
        if state_a is None:
            raise ValueError(
                f"ISL feasibility missing propagated state for endpoint {node_id!r}; "
                "refusing to convert absent physics into a no-link decision"
            )

        for assignment_a in by_node.get(node_id, []):
            peer_id = assignment_a.peer_node_id
            state_b = sat_states.get(peer_id)
            if state_b is None:
                raise ValueError(
                    f"ISL feasibility missing propagated state for endpoint {peer_id!r} "
                    f"while evaluating {node_id!r}->{peer_id!r}; refusing to convert "
                    "absent physics into a no-link decision"
                )

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
            if state_a.central_body not in body_frames or state_b.central_body not in body_frames:
                raise ValueError(
                    f"ISL feasibility is missing resolved body primitive facts for "
                    f"{state_a.central_body!r} or {state_b.central_body!r}"
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
            pos_a, _vel_a, pos_b, _vel_b = _state_vectors(state_a, state_b)
            range_km = compute_range_km(pos_a, pos_b)
            common = {
                "pair": pair,
                "link_type": assignment_a.link_type,
                "constraints_a": constraints_a,
                "constraints_b": constraints_b,
                "interface_a": assignment_a.interface,
                "interface_b": assignment_b.interface,
                "range_km": range_km,
                "max_range_km": max_range_km,
                "applied_tracking_rate": applied_tracking_rate,
                "field_of_regard_deg": field_of_regard_deg,
            }

            if constraints_a.terminal_type != constraints_b.terminal_type:
                rejected.append(
                    (
                        common,
                        "terminal_type_mismatch",
                        f"{constraints_a.terminal_type}/{constraints_b.terminal_type}",
                    )
                )
                continue
            if not _role_allows_link(
                constraints_a.role, assignment_a.link_type
            ) or not _role_allows_link(constraints_b.role, assignment_a.link_type):
                rejected.append((common, "terminal_role_mismatch", constraints_a.terminal_type))
                continue

            candidates.append(
                _Candidate(
                    pair=pair,
                    link_type=assignment_a.link_type,
                    constraints_a=constraints_a,
                    constraints_b=constraints_b,
                    interface_a=assignment_a.interface,
                    interface_b=assignment_b.interface,
                    range_km=range_km,
                    limits=IslLimits(
                        max_range_km=max_range_km,
                        field_of_regard_deg=field_of_regard_deg,
                        max_tracking_rate_deg_s=applied_tracking_rate,
                        polar_seam=polar_seam_enabled
                        and assignment_a.link_type == "cross_plane_isl"
                        and state_a.central_body == state_b.central_body,
                    ),
                    applied_tracking_rate=applied_tracking_rate,
                )
            )

    all_pairs = [common["pair"] for common, _reason, _type in rejected] + [
        candidate.pair for candidate in candidates
    ]
    if not all_pairs:
        return results
    # Each direction's one-way light time of a signal sent at this tick.
    forward, backward = signals.directions(all_pairs)
    one_way = {
        pair: (float(ahead) * 1000.0, float(back) * 1000.0)
        for pair, ahead, back in zip(
            all_pairs, forward.light_time_s, backward.light_time_s, strict=True
        )
    }
    for common, reason, terminal_type in rejected:
        pair = common["pair"]
        results[pair] = _result(
            reason=reason, terminal_type=terminal_type, one_way_ms=one_way[pair], **common
        )
    if not candidates:
        return results
    reasons = isl_pair_reasons(
        signals=signals,
        body_frames=body_frames,
        pairs=[candidate.pair for candidate in candidates],
        limits=[candidate.limits for candidate in candidates],
        latitude_threshold_deg=latitude_threshold_deg,
    )
    for candidate, reason in zip(candidates, reasons, strict=True):
        results[candidate.pair] = _result(
            pair=candidate.pair,
            link_type=candidate.link_type,
            constraints_a=candidate.constraints_a,
            constraints_b=candidate.constraints_b,
            interface_a=candidate.interface_a,
            interface_b=candidate.interface_b,
            range_km=candidate.range_km,
            one_way_ms=one_way[candidate.pair],
            reason=reason,
            terminal_type=candidate.constraints_a.terminal_type,
            max_range_km=candidate.limits.max_range_km,
            applied_tracking_rate=candidate.applied_tracking_rate,
            field_of_regard_deg=candidate.limits.field_of_regard_deg,
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
            one_way_a_to_b_ms=result.one_way_a_to_b_ms,
            one_way_b_to_a_ms=result.one_way_b_to_a_ms,
            scheduled=is_scheduled,
            unscheduled_reason=reason,
        )

    return scheduled
