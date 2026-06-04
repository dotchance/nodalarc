# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-link allocation engine.

This module owns the OME allocation role for ground links: given physical
visibility decisions and prior scheduling state, it applies operator-selected
selection and handover policy to produce the authoritative terminal allocation
for one tick. It does not propagate or evaluate orbital physics, and it does
not publish NATS events.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nodalarc.models.ground_policy import (
    CrossTenantDisplacementPolicy,
    HandoverPolicySpec,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicySpec,
    SuccessorAbortPolicy,
)
from nodalarc.models.ground_station import HysteresisParameters
from nodalarc.models.link_decisions import (
    GroundAllocationEvent,
    GroundAllocationEventCategory,
    GroundAllocationPolicyKind,
    GroundAllocationPolicyName,
    GroundPolicyAudit,
    GroundUnscheduledReason,
    UnscheduledPair,
)

from ome.ground_handover_policies import (
    HandoverContext,
    compute_effective_hysteresis_discount,
    evaluate_handover,
)
from ome.ground_selection_policies import SelectionContext, score_candidate
from ome.types import MbbTeardown, MbbTeardownLifecycleEvent, MbbTeardownState
from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class GroundAllocationResult:
    """Result of the ground allocation pass for one OME tick.

    ``unscheduled_pairs`` carries one entry per visible pair that the allocator
    considered but did not schedule, with the attributed reason. Active MBB
    teardown pairs remain scheduled and are represented in ``pending_teardowns``
    rather than as unscheduled pairs.
    """

    associations: dict[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    scheduled_pairs: frozenset[tuple[str, str]]
    unscheduled_pairs: tuple[UnscheduledPair, ...]
    policy_audit: GroundPolicyAudit
    allocation_events: tuple[GroundAllocationEvent, ...]
    lifecycle_events: tuple[MbbTeardownLifecycleEvent, ...] = ()


@dataclass(frozen=True)
class _Candidate:
    pair: tuple[str, str]
    gs_id: str
    sat_id: str
    visibility: GroundVisibility
    selection_score: float
    service_priority: int
    per_gs_rank: int
    satellite_ground_terminal_capacity: int
    rank_key: tuple


@dataclass(frozen=True)
class _Rejected:
    reason: GroundUnscheduledReason
    incumbent_pair: tuple[str, str] | None
    capacity_constraint: str | None


# Terminal handover failure/release reasons are final for the tick. Later
# allocator passes may reconsider capacity or hysteresis rejections after a
# same-tick release, but these state-machine outcomes must not be overwritten.
_LOCKED_REJECTION_REASONS: frozenset[GroundUnscheduledReason] = frozenset(
    {
        "replaced_by_successor",
        "successor_aborted",
        "failed_successor",
        "failed_acquire",
    }
)


def _compute_pair_score(
    elevation_deg: float,
    policy: str,
    remaining_visible_s: float | None = None,
) -> float:
    """Legacy test helper for pure selection scoring. Higher is better."""

    if policy == "highest-elevation":
        return elevation_deg
    if policy == "lowest-elevation":
        return 90.0 - elevation_deg
    if policy == "longest-remaining-pass":
        if remaining_visible_s is None:
            raise ValueError(
                "Ground scheduling policy 'longest-remaining-pass' requires "
                "OME pass lookahead; missing remaining_visible_s"
            )
        return remaining_visible_s
    raise ValueError(f"Unknown ground scheduling policy: {policy!r}")


def _compute_effective_discount(
    elevation_deg: float,
    min_elevation_deg: float,
    hyst: HysteresisParameters,
) -> float:
    """Legacy test helper for hysteresis hold-score calculation."""

    return compute_effective_hysteresis_discount(
        elevation_deg=elevation_deg,
        min_elevation_deg=min_elevation_deg,
        params=hyst,
    )


def _normalized_pair(gs_id: str, sat_id: str) -> tuple[str, str]:
    return (min(gs_id, sat_id), max(gs_id, sat_id))


def _ground_and_satellite_ids(
    pair: tuple[str, str],
    ground_station_ids: set[str],
) -> tuple[str, str]:
    """Return `(ground_station_id, satellite_id)` for a normalized pair."""

    if pair[0] in ground_station_ids and pair[1] in ground_station_ids:
        raise ValueError(f"Pair {pair!r} contains two ground-station ids")
    if pair[0] in ground_station_ids:
        return pair[0], pair[1]
    if pair[1] in ground_station_ids:
        return pair[1], pair[0]
    raise ValueError(f"Pair {pair!r} does not contain a known ground-station id")


def _validate_ranking_order(
    ranking_order: Sequence[RankingComponent],
) -> tuple[RankingComponent, ...]:
    order = tuple(ranking_order)
    if not order:
        raise ValueError("ground allocator ranking_order must not be empty")
    if order[-1] != "lex_pair":
        raise ValueError("ground allocator ranking_order must end with 'lex_pair'")
    if len(order) == 1:
        raise ValueError(
            "ground allocator ranking_order needs a decision component before lex_pair"
        )
    if len(set(order)) != len(order):
        raise ValueError("ground allocator ranking_order must not contain duplicates")
    return order


def _candidate_rank_key(
    *,
    candidate: _Candidate,
    ranking_order: Sequence[RankingComponent],
) -> tuple:
    values: list[object] = []
    for component in ranking_order:
        if component == "service_priority":
            values.append(candidate.service_priority)
        elif component == "selection_score":
            values.append(-candidate.selection_score)
        elif component == "per_gs_rank":
            values.append(candidate.per_gs_rank)
        elif component == "satellite_ground_terminal_capacity":
            values.append(candidate.satellite_ground_terminal_capacity)
        elif component == "lex_pair":
            values.append(candidate.pair)
        else:  # pragma: no cover - Literal/model validation should prevent this.
            raise ValueError(f"Unknown ranking component {component!r}")
    return tuple(values)


def _build_candidates(
    *,
    step: int,
    visible_per_station: Mapping[str, list[GroundVisibility]],
    gs_selection_policies: Mapping[str, SelectionPolicySpec],
    gs_service_priorities: Mapping[str, int],
    gs_reference_bodies: Mapping[str, str],
    sat_terminal_pools: Mapping[str, Mapping[str, Sequence[int]]],
    ranking_order: Sequence[RankingComponent],
) -> tuple[list[_Candidate], dict[tuple[str, str], _Candidate], set[tuple[str, str]]]:
    by_gs: dict[str, list[_Candidate]] = {}
    visible_set: set[tuple[str, str]] = set()
    candidate_by_pair: dict[tuple[str, str], _Candidate] = {}

    for gs_id, visible_sats in sorted(visible_per_station.items()):
        if gs_id not in gs_selection_policies:
            raise ValueError(f"Ground allocator is missing selection policy for {gs_id}")
        if gs_id not in gs_service_priorities:
            raise ValueError(f"Ground allocator is missing service_priority for {gs_id}")
        station_candidates: list[_Candidate] = []
        for gv in visible_sats:
            if not gv.visible:
                raise ValueError(
                    f"visible_per_station[{gs_id!r}] contains invisible satellite "
                    f"{gv.sat_id!r}; visibility filtering belongs to the physics role"
                )
            pair = _normalized_pair(gs_id, gv.sat_id)
            if pair in visible_set:
                raise ValueError(f"Duplicate ground visibility candidate for pair {pair!r}")
            visible_set.add(pair)
            policy = gs_selection_policies[gs_id]
            score = score_candidate(
                policy=policy,
                visibility=gv,
                context=SelectionContext(step=step, gs_id=gs_id, sat_id=gv.sat_id),
            )
            reference_body = gs_reference_bodies[gs_id]
            station_candidates.append(
                _Candidate(
                    pair=pair,
                    gs_id=gs_id,
                    sat_id=gv.sat_id,
                    visibility=gv,
                    selection_score=score,
                    service_priority=gs_service_priorities[gs_id],
                    per_gs_rank=-1,
                    satellite_ground_terminal_capacity=len(
                        sat_terminal_pools.get(gv.sat_id, {}).get(reference_body, ())
                    ),
                    rank_key=(),
                )
            )
        by_gs[gs_id] = station_candidates

    ranked: list[_Candidate] = []
    for _gs_id, station_candidates in by_gs.items():
        station_candidates.sort(key=lambda c: (-c.selection_score, c.pair))
        for idx, candidate in enumerate(station_candidates):
            ranked_candidate = _Candidate(
                pair=candidate.pair,
                gs_id=candidate.gs_id,
                sat_id=candidate.sat_id,
                visibility=candidate.visibility,
                selection_score=candidate.selection_score,
                service_priority=candidate.service_priority,
                per_gs_rank=idx,
                satellite_ground_terminal_capacity=candidate.satellite_ground_terminal_capacity,
                rank_key=(),
            )
            ranked.append(ranked_candidate)

    ranked_with_keys: list[_Candidate] = []
    for candidate in ranked:
        keyed = _Candidate(
            pair=candidate.pair,
            gs_id=candidate.gs_id,
            sat_id=candidate.sat_id,
            visibility=candidate.visibility,
            selection_score=candidate.selection_score,
            service_priority=candidate.service_priority,
            per_gs_rank=candidate.per_gs_rank,
            satellite_ground_terminal_capacity=candidate.satellite_ground_terminal_capacity,
            rank_key=_candidate_rank_key(candidate=candidate, ranking_order=ranking_order),
        )
        ranked_with_keys.append(keyed)
        candidate_by_pair[keyed.pair] = keyed

    ranked_with_keys.sort(key=lambda c: c.rank_key)
    return ranked_with_keys, candidate_by_pair, visible_set


def _terminal_totals_for_pair(
    *,
    pair: tuple[str, str],
    ground_station_ids: set[str],
    gs_terminal_counts: Mapping[str, int],
    sat_ground_terminals: Mapping[str, int],
) -> tuple[str, str, int, int]:
    gs_id, sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
    if gs_id not in gs_terminal_counts:
        raise ValueError(f"Missing ground terminal count for {gs_id}")
    if sat_id not in sat_ground_terminals:
        raise ValueError(f"Missing satellite ground terminal count for {sat_id}")
    return gs_id, sat_id, gs_terminal_counts[gs_id], sat_ground_terminals[sat_id]


def _record_rejection(
    rejected: dict[tuple[str, str], _Rejected],
    pair: tuple[str, str],
    *,
    reason: GroundUnscheduledReason,
    incumbent_pair: tuple[str, str] | None,
    capacity_constraint: str | None,
) -> None:
    rejected[pair] = _Rejected(
        reason=reason,
        incumbent_pair=incumbent_pair,
        capacity_constraint=capacity_constraint,
    )


def _make_event(
    *,
    category: GroundAllocationEventCategory,
    pair: tuple[str, str],
    gs_id: str,
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    message: str,
    successor_pair: tuple[str, str] | None = None,
    challenger_pair: tuple[str, str] | None = None,
    policy_kind: GroundAllocationPolicyKind | None = None,
    policy_name: GroundAllocationPolicyName | None = None,
) -> GroundAllocationEvent:
    return GroundAllocationEvent(
        category=category,
        pair=pair,
        tenant_id=gs_tenant_ids[gs_id],
        reference_body=gs_reference_bodies[gs_id],
        message=message,
        successor_pair=successor_pair,
        challenger_pair=challenger_pair,
        policy_kind=policy_kind,
        policy_name=policy_name,
    )


def _policy_audit(
    *,
    gs_selection_policies: Mapping[str, SelectionPolicySpec],
    gs_handover_policies: Mapping[str, HandoverPolicySpec],
    ranking_order: Sequence[RankingComponent],
    gs_handover_modes: Mapping[str, Literal["bbm", "mbb"]],
    gs_mbb_overlap_ticks: Mapping[str, int],
    gs_mbb_reserve: Mapping[str, int],
    mbb_preemption: MbbPreemptionPolicy,
    successor_abort_policy: SuccessorAbortPolicy,
    cross_tenant_displacement: CrossTenantDisplacementPolicy,
    bbm_acquire_timeout_ticks: int,
    ignored_capacity_fields: Sequence[str],
) -> GroundPolicyAudit:
    handover_mode_values = set(gs_handover_modes.values())
    return GroundPolicyAudit(
        selection_policies={k: v.name for k, v in sorted(gs_selection_policies.items())},
        selection_policy_params={
            k: dict(v.params) for k, v in sorted(gs_selection_policies.items())
        },
        handover_policies={k: v.name for k, v in sorted(gs_handover_policies.items())},
        handover_policy_params={k: dict(v.params) for k, v in sorted(gs_handover_policies.items())},
        ranking_order=tuple(ranking_order),
        handover_mode=(
            next(iter(handover_mode_values)) if len(handover_mode_values) == 1 else "mixed"
        ),
        handover_modes=dict(sorted(gs_handover_modes.items())),
        mbb_preemption=mbb_preemption,
        successor_abort_policy=successor_abort_policy,
        cross_tenant_displacement=cross_tenant_displacement,
        mbb_overlap_ticks=max(gs_mbb_overlap_ticks.values(), default=0),
        mbb_overlap_ticks_by_gs=dict(sorted(gs_mbb_overlap_ticks.items())),
        mbb_reserve=max(gs_mbb_reserve.values(), default=0),
        mbb_reserve_by_gs=dict(sorted(gs_mbb_reserve.items())),
        bbm_acquire_timeout_ticks=bbm_acquire_timeout_ticks,
        ignored_capacity_fields=tuple(sorted(ignored_capacity_fields)),
    )


def _normalize_satellite_terminal_pools(
    *,
    sat_ground_terminals: Mapping[str, int],
    sat_ground_terminal_indices_by_body: Mapping[str, Mapping[str, Sequence[int]]],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """Validate the satellite terminal indices available for each target body."""

    missing = sorted(set(sat_ground_terminals) - set(sat_ground_terminal_indices_by_body))
    if missing:
        raise ValueError(
            "Ground allocator is missing satellite ground-terminal index pools for "
            + ", ".join(missing)
        )

    normalized: dict[str, dict[str, tuple[int, ...]]] = {}
    for sat_id, total in sorted(sat_ground_terminals.items()):
        if total < 0:
            raise ValueError(f"Satellite {sat_id} ground terminal count must be >= 0")
        body_map = sat_ground_terminal_indices_by_body[sat_id]
        if total > 0 and not body_map:
            raise ValueError(
                f"Satellite {sat_id} has {total} ground terminal(s) but no target-body pools"
            )
        normalized[sat_id] = {}
        for reference_body, indices in sorted(body_map.items()):
            pool = tuple(int(idx) for idx in indices)
            if len(set(pool)) != len(pool):
                raise ValueError(
                    f"Satellite {sat_id} target_body={reference_body!r} terminal pool "
                    "contains duplicate indices"
                )
            invalid = [idx for idx in pool if idx < 0 or idx >= total]
            if invalid:
                raise ValueError(
                    f"Satellite {sat_id} target_body={reference_body!r} terminal pool "
                    f"contains out-of-range indices {invalid}; valid range is 0..{total - 1}"
                )
            normalized[sat_id][str(reference_body)] = pool
    return normalized


def _visible_incumbent_for_pair(
    *,
    pair: tuple[str, str],
    candidate_by_pair: Mapping[tuple[str, str], _Candidate],
) -> _Candidate:
    candidate = candidate_by_pair.get(pair)
    if candidate is None:
        raise ValueError(
            f"Internal allocator invariant violated: current association {pair!r} "
            "is visible but has no selection candidate."
        )
    return candidate


def allocate_ground_links(
    *,
    step: int,
    visible_per_station: Mapping[str, list[GroundVisibility]],
    ground_station_ids: set[str],
    current_associations: Mapping[tuple[str, str], tuple[int, int]],
    pending_teardowns: MbbTeardownState,
    gs_terminal_counts: Mapping[str, int],
    gs_selection_policies: Mapping[str, SelectionPolicySpec],
    gs_min_elevations: Mapping[str, float],
    gs_handover_policies: Mapping[str, HandoverPolicySpec],
    gs_handover_modes: Mapping[str, Literal["bbm", "mbb"]],
    gs_mbb_overlap_ticks: Mapping[str, int],
    gs_mbb_reserve: Mapping[str, int],
    gs_service_priorities: Mapping[str, int],
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    sat_ground_terminals: Mapping[str, int],
    sat_ground_terminal_indices_by_body: Mapping[str, Mapping[str, Sequence[int]]],
    ranking_order: Sequence[RankingComponent],
    mbb_preemption: MbbPreemptionPolicy,
    successor_abort_policy: SuccessorAbortPolicy,
    cross_tenant_displacement: CrossTenantDisplacementPolicy,
    bbm_acquire_timeout_ticks: int,
    ignored_capacity_fields: Sequence[str],
) -> GroundAllocationResult:
    """Allocate ground links for one tick.

    This function is the state-machine mechanism. Operator policy enters only
    through named policy specs, ranking_order, and handover mode parameters.
    Scheduler state, Node Agent state, and orbital physics do not enter this
    function.
    """

    if step < 0:
        raise ValueError("Ground allocator step must be non-negative")
    if mbb_preemption != "off":
        raise ValueError(
            f"mbb_preemption={mbb_preemption!r} is not implemented; "
            "the schema must reject unsupported preemption policies"
        )
    if cross_tenant_displacement != "off":
        raise ValueError(
            f"cross_tenant_displacement={cross_tenant_displacement!r} is not implemented "
            "yet; cross-tenant preemption needs an explicit priority policy"
        )
    if successor_abort_policy not in ("hard_release", "soft_retain"):
        raise ValueError(f"Unknown successor_abort_policy={successor_abort_policy!r}")
    if bbm_acquire_timeout_ticks != 1:
        raise ValueError(
            "bbm_acquire_timeout_ticks values other than 1 require the future "
            "multi-tick BBMGap wait-state algorithm"
        )
    order = _validate_ranking_order(ranking_order)

    known_gs = set(ground_station_ids) | set(gs_terminal_counts)
    for label, mapping in (
        ("handover mode", gs_handover_modes),
        ("MBB overlap ticks", gs_mbb_overlap_ticks),
        ("MBB reserve", gs_mbb_reserve),
    ):
        missing = sorted(known_gs - set(mapping))
        extra = sorted(set(mapping) - known_gs)
        if missing or extra:
            detail = []
            if missing:
                detail.append("missing=" + ", ".join(missing))
            if extra:
                detail.append("extra=" + ", ".join(extra))
            raise ValueError(
                f"Ground allocator {label} map does not match stations: {'; '.join(detail)}"
            )

    for gs_id in sorted(known_gs):
        mode = gs_handover_modes[gs_id]
        if mode not in ("bbm", "mbb"):
            raise ValueError(f"Unknown handover_mode for {gs_id}: {mode!r}")
        overlap_ticks = gs_mbb_overlap_ticks[gs_id]
        reserve = gs_mbb_reserve[gs_id]
        if overlap_ticks < 0:
            raise ValueError(f"mbb_overlap_ticks for {gs_id} must be >= 0")
        if reserve < 0:
            raise ValueError(f"mbb_reserve for {gs_id} must be >= 0")
        # BIG HONESTY NOTE / MBB-002:
        # This allocator has a deliberate single-overlap state machine per GS. When a
        # GS is already in MBBOverlap, new challengers are rejected as
        # `mbb_overlap_locked`; a second reserved terminal is not consumed for a second
        # parallel overlap. Letting mbb_reserve=2+ through would silently strand
        # capacity and lie about supported gateway behavior. Remove this guard only
        # when MBB-002 adds multi-overlap pending-teardown state and proves it.
        if reserve > 1:
            raise ValueError(
                f"mbb_reserve > 1 for {gs_id} requires future MBB-002 multi-overlap "
                "allocator support; current allocator supports at most one concurrent "
                "MBB overlap per GS"
            )
        if mode == "mbb":
            if overlap_ticks <= 0 or reserve <= 0:
                raise ValueError(
                    f"MBB handover for {gs_id} requires mbb_overlap_ticks > 0 and mbb_reserve > 0"
                )
            if gs_terminal_counts[gs_id] <= reserve:
                raise ValueError(
                    f"MBB handover for {gs_id} requires terminal capacity greater "
                    f"than mbb_reserve; capacity={gs_terminal_counts[gs_id]}, reserve={reserve}"
                )
        elif reserve != 0 or overlap_ticks != 0:
            raise ValueError(
                f"BBM handover for {gs_id} must carry mbb_reserve=0 and mbb_overlap_ticks=0"
            )

    visible_gs = set(visible_per_station)
    missing_tenant = sorted(visible_gs - set(gs_tenant_ids))
    if missing_tenant:
        raise ValueError(
            "Ground allocator is missing tenant_id for "
            f"{', '.join(missing_tenant)}; every unscheduled pair needs tenant scope"
        )
    missing_body = sorted(visible_gs - set(gs_reference_bodies))
    if missing_body:
        raise ValueError(
            "Ground allocator is missing reference_body for "
            f"{', '.join(missing_body)}; every unscheduled pair needs a body anchor"
        )
    missing_handover = sorted(visible_gs - set(gs_handover_policies))
    if missing_handover:
        raise ValueError(
            f"Ground allocator is missing handover_policy for {', '.join(missing_handover)}"
        )
    missing_min_elevation = sorted(visible_gs - set(gs_min_elevations))
    if missing_min_elevation:
        raise ValueError(
            f"Ground allocator is missing min elevation for {', '.join(missing_min_elevation)}"
        )

    policy_audit = _policy_audit(
        gs_selection_policies=gs_selection_policies,
        gs_handover_policies=gs_handover_policies,
        ranking_order=order,
        gs_handover_modes=gs_handover_modes,
        gs_mbb_overlap_ticks=gs_mbb_overlap_ticks,
        gs_mbb_reserve=gs_mbb_reserve,
        mbb_preemption=mbb_preemption,
        successor_abort_policy=successor_abort_policy,
        cross_tenant_displacement=cross_tenant_displacement,
        bbm_acquire_timeout_ticks=bbm_acquire_timeout_ticks,
        ignored_capacity_fields=ignored_capacity_fields,
    )

    sat_terminal_pools = _normalize_satellite_terminal_pools(
        sat_ground_terminals=sat_ground_terminals,
        sat_ground_terminal_indices_by_body=sat_ground_terminal_indices_by_body,
    )

    candidates, candidate_by_pair, visible_set = _build_candidates(
        step=step,
        visible_per_station=visible_per_station,
        gs_selection_policies=gs_selection_policies,
        gs_service_priorities=gs_service_priorities,
        gs_reference_bodies=gs_reference_bodies,
        sat_terminal_pools=sat_terminal_pools,
        ranking_order=order,
    )
    gs_occupied: dict[str, set[int]] = {gs: set() for gs in gs_terminal_counts}
    sat_occupied: dict[str, set[int]] = {sat: set() for sat in sat_ground_terminals}
    new_associations: dict[tuple[str, str], tuple[int, int]] = {}
    new_pending_teardowns: MbbTeardownState = {}
    rejected: dict[tuple[str, str], _Rejected] = {}
    allocation_events: list[GroundAllocationEvent] = []
    lifecycle_events: list[MbbTeardownLifecycleEvent] = []
    drop_current_pairs: set[tuple[str, str]] = set()

    def add_existing(pair: tuple[str, str], indices: tuple[int, int]) -> None:
        gs_id, sat_id, gs_total, sat_total = _terminal_totals_for_pair(
            pair=pair,
            ground_station_ids=ground_station_ids,
            gs_terminal_counts=gs_terminal_counts,
            sat_ground_terminals=sat_ground_terminals,
        )
        gs_idx, sat_idx = indices
        if not 0 <= gs_idx < gs_total:
            raise ValueError(f"Association {pair!r} has invalid GS terminal index {gs_idx}")
        if not 0 <= sat_idx < sat_total:
            raise ValueError(f"Association {pair!r} has invalid satellite terminal index {sat_idx}")
        if gs_idx in gs_occupied.setdefault(gs_id, set()):
            raise ValueError(f"Duplicate GS terminal occupancy {gs_id}.term{gs_idx}")
        reference_body = gs_reference_bodies[gs_id]
        sat_pool = sat_terminal_pools[sat_id].get(reference_body, ())
        if sat_idx not in sat_pool:
            raise ValueError(
                f"Association {pair!r} uses satellite terminal index {sat_idx}, but "
                f"{sat_id}.ground_terminals has no such index for "
                f"reference_body={reference_body!r}"
            )
        if sat_idx in sat_occupied.setdefault(sat_id, set()):
            raise ValueError(f"Duplicate satellite ground terminal occupancy {sat_id}.gnd{sat_idx}")
        gs_occupied[gs_id].add(gs_idx)
        sat_occupied[sat_id].add(sat_idx)
        new_associations[pair] = indices

    def remove_association(pair: tuple[str, str]) -> tuple[int, int]:
        indices = new_associations.pop(pair)
        gs_id, sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
        gs_idx, sat_idx = indices
        gs_occupied.setdefault(gs_id, set()).discard(gs_idx)
        sat_occupied.setdefault(sat_id, set()).discard(sat_idx)
        new_pending_teardowns.pop(pair, None)
        return indices

    def sat_terminal_pool(candidate: _Candidate) -> tuple[int, ...]:
        reference_body = gs_reference_bodies[candidate.gs_id]
        return sat_terminal_pools.get(candidate.sat_id, {}).get(reference_body, ())

    def sat_capacity_constraint(candidate: _Candidate) -> str:
        reference_body = gs_reference_bodies[candidate.gs_id]
        return f"{candidate.sat_id}.ground_terminals[{reference_body}]"

    def next_sat_terminal_index(candidate: _Candidate) -> int | None:
        occupied = sat_occupied.setdefault(candidate.sat_id, set())
        return next((idx for idx in sat_terminal_pool(candidate) if idx not in occupied), None)

    def satellite_has_capacity(candidate: _Candidate) -> bool:
        return next_sat_terminal_index(candidate) is not None

    def allocate_new(candidate: _Candidate) -> bool:
        gs_occ = gs_occupied.setdefault(candidate.gs_id, set())
        sat_occ = sat_occupied.setdefault(candidate.sat_id, set())
        gs_total = gs_terminal_counts[candidate.gs_id]
        gs_idx = next((i for i in range(gs_total) if i not in gs_occ), None)
        sat_idx = next_sat_terminal_index(candidate)
        if gs_idx is None or sat_idx is None:
            return False
        gs_occ.add(gs_idx)
        sat_occ.add(sat_idx)
        new_associations[candidate.pair] = (gs_idx, sat_idx)
        rejected.pop(candidate.pair, None)
        return True

    def handover_mode_for(gs_id: str) -> Literal["bbm", "mbb"]:
        return gs_handover_modes[gs_id]

    def mbb_overlap_ticks_for(gs_id: str) -> int:
        return gs_mbb_overlap_ticks[gs_id] if handover_mode_for(gs_id) == "mbb" else 0

    def mbb_reserve_for(gs_id: str) -> int:
        return gs_mbb_reserve[gs_id] if handover_mode_for(gs_id) == "mbb" else 0

    def steady_limit(gs_id: str) -> int:
        tc = gs_terminal_counts[gs_id]
        return tc - mbb_reserve_for(gs_id) if handover_mode_for(gs_id) == "mbb" else tc

    def steady_pairs_for_gs(gs_id: str) -> list[tuple[str, str]]:
        return [
            pair
            for pair in new_associations
            if pair not in new_pending_teardowns
            and _ground_and_satellite_ids(pair, ground_station_ids)[0] == gs_id
        ]

    def worst_steady_incumbent(gs_id: str) -> _Candidate | None:
        incumbents = [
            _visible_incumbent_for_pair(pair=pair, candidate_by_pair=candidate_by_pair)
            for pair in steady_pairs_for_gs(gs_id)
        ]
        if not incumbents:
            return None
        return max(incumbents, key=lambda c: c.rank_key)

    def rejection_locked(pair: tuple[str, str]) -> bool:
        rejection = rejected.get(pair)
        return rejection is not None and rejection.reason in _LOCKED_REJECTION_REASONS

    def protected_mbb_pairs() -> set[tuple[str, str]]:
        protected = set(new_pending_teardowns)
        protected.update(teardown.successor_pair for teardown in new_pending_teardowns.values())
        return protected

    def protected_mbb_pair_for_gs(gs_id: str) -> tuple[str, str] | None:
        for pair in sorted(protected_mbb_pairs()):
            pair_gs, _pair_sat = _ground_and_satellite_ids(pair, ground_station_ids)
            if pair_gs == gs_id:
                return pair
        return None

    def same_partition(candidate: _Candidate, pair: tuple[str, str]) -> bool:
        pair_gs, _pair_sat = _ground_and_satellite_ids(pair, ground_station_ids)
        return (
            gs_tenant_ids[pair_gs] == gs_tenant_ids[candidate.gs_id]
            and gs_reference_bodies[pair_gs] == gs_reference_bodies[candidate.gs_id]
        )

    def sat_occupants_for_candidate(candidate: _Candidate) -> list[_Candidate]:
        target_pool = set(sat_terminal_pool(candidate))
        occupants: list[_Candidate] = []
        for pair, (_gs_idx, sat_idx) in sorted(new_associations.items()):
            _pair_gs, pair_sat = _ground_and_satellite_ids(pair, ground_station_ids)
            if pair_sat == candidate.sat_id and sat_idx in target_pool:
                occupants.append(
                    _visible_incumbent_for_pair(pair=pair, candidate_by_pair=candidate_by_pair)
                )
        return occupants

    def sat_capacity_blocker(candidate: _Candidate) -> tuple[str, str] | None:
        occupants = sat_occupants_for_candidate(candidate)
        if not occupants:
            return None
        return max(occupants, key=lambda c: c.rank_key).pair

    def displaceable_sat_incumbent(candidate: _Candidate) -> _Candidate | None:
        protected = protected_mbb_pairs()
        incumbents = [
            incumbent
            for incumbent in sat_occupants_for_candidate(candidate)
            if incumbent.pair not in protected
            and same_partition(candidate, incumbent.pair)
            and candidate.rank_key < incumbent.rank_key
        ]
        if not incumbents:
            return None
        return max(incumbents, key=lambda c: c.rank_key)

    def emit_bbm_gap(incumbent: _Candidate, challenger: _Candidate) -> None:
        allocation_events.append(
            _make_event(
                category="bbm_gap",
                pair=incumbent.pair,
                gs_id=challenger.gs_id,
                gs_tenant_ids=gs_tenant_ids,
                gs_reference_bodies=gs_reference_bodies,
                message=(
                    f"BBM released incumbent {incumbent.pair!r} before acquiring "
                    f"challenger {challenger.pair!r}; timeout_ticks={bbm_acquire_timeout_ticks}. "
                    "BBMGap is an immediate one-tick release/acquire transition."
                ),
                successor_pair=challenger.pair,
                challenger_pair=challenger.pair,
                policy_kind="handover_mode",
                policy_name="bbm",
            )
        )

    def emit_incumbent_lost(pair: tuple[str, str], message: str) -> None:
        gs_id, _sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
        allocation_events.append(
            _make_event(
                category="incumbent_lost",
                pair=pair,
                gs_id=gs_id,
                gs_tenant_ids=gs_tenant_ids,
                gs_reference_bodies=gs_reference_bodies,
                message=message,
            )
        )

    def authority_before_for_pairs(
        old_pair: tuple[str, str], successor_pair: tuple[str, str]
    ) -> dict[str, dict[str, object]]:
        def one(pair: tuple[str, str]) -> dict[str, object]:
            indices = current_associations.get(pair)
            return {
                "pair": list(pair),
                "scheduled": pair in current_associations,
                "pending_teardown": pair in pending_teardowns,
                "visible": pair in visible_set,
                "terminal_indices": list(indices) if indices is not None else None,
            }

        return {
            "old_pair": one(old_pair),
            "successor_pair": one(successor_pair),
        }

    def emit_mbb_lifecycle_terminal(
        *,
        category: GroundAllocationEventCategory,
        old_pair: tuple[str, str],
        successor_pair: tuple[str, str],
        gs_id: str,
        message: str,
        source_allocation_event_category: GroundAllocationEventCategory | None = None,
    ) -> None:
        lifecycle_events.append(
            MbbTeardownLifecycleEvent(
                category=category,
                old_pair=old_pair,
                successor_pair=successor_pair,
                gs_id=gs_id,
                message=message,
                source_allocation_event_category=source_allocation_event_category or category,
                authority_before=authority_before_for_pairs(old_pair, successor_pair),
                terminal_indices={
                    key: tuple(value)
                    for key, value in (
                        ("old_pair", current_associations.get(old_pair)),
                        ("successor_pair", current_associations.get(successor_pair)),
                    )
                    if value is not None
                },
            )
        )

    # Prior-state recovery: resolve in-flight MBB teardown state before new
    # candidates compete. This is a state-machine transition, not selection.
    for pair in sorted(pending_teardowns):
        if pair not in current_associations:
            raise ValueError(f"Pending teardown {pair!r} is missing from current associations")
        teardown = pending_teardowns[pair]
        if teardown.start_step > step:
            raise ValueError(f"Pending teardown {pair!r} starts in the future")
        gs_id, _sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
        successor = teardown.successor_pair
        if successor == pair:
            raise ValueError(f"Pending teardown {pair!r} names itself as successor")
        pair_visible = pair in visible_set
        successor_visible = successor in visible_set
        successor_current = successor in current_associations and successor not in pending_teardowns
        elapsed = step - teardown.start_step
        if handover_mode_for(gs_id) != "mbb":
            raise ValueError(
                f"Pending MBB teardown {pair!r} exists for BBM ground station {gs_id}; "
                "runtime handover policy and allocator state are inconsistent"
            )

        if not successor_visible or not successor_current:
            category = "failed_successor" if not successor_current else "successor_aborted"
            message = (
                f"MBB successor {successor!r} did not survive prior-state recovery; "
                f"successor_abort_policy={successor_abort_policy}"
            )
            allocation_events.append(
                _make_event(
                    category=category,
                    pair=pair,
                    gs_id=gs_id,
                    gs_tenant_ids=gs_tenant_ids,
                    gs_reference_bodies=gs_reference_bodies,
                    message=message,
                    successor_pair=successor,
                    policy_kind="successor_abort_policy",
                    policy_name=successor_abort_policy,
                )
            )
            emit_mbb_lifecycle_terminal(
                category=category,
                old_pair=pair,
                successor_pair=successor,
                gs_id=gs_id,
                message=message,
            )
            if not pair_visible:
                emit_incumbent_lost(
                    pair,
                    f"Scheduled MBB incumbent {pair!r} lost physical visibility during "
                    "prior-state recovery",
                )
            if successor_current and not successor_visible:
                emit_incumbent_lost(
                    successor,
                    f"Scheduled MBB successor {successor!r} lost physical visibility "
                    "during prior-state recovery",
                )
            if successor_visible and not successor_current:
                successor_gs, _successor_sat = _ground_and_satellite_ids(
                    successor, ground_station_ids
                )
                allocation_events.append(
                    _make_event(
                        category="failed_acquire",
                        pair=successor,
                        gs_id=successor_gs,
                        gs_tenant_ids=gs_tenant_ids,
                        gs_reference_bodies=gs_reference_bodies,
                        message=(
                            f"MBB successor {successor!r} was visible but was not present "
                            "in current associations during prior-state recovery"
                        ),
                        successor_pair=successor,
                        policy_kind="successor_abort_policy",
                        policy_name=successor_abort_policy,
                    )
                )
            drop_current_pairs.add(pair)
            drop_current_pairs.add(successor)
            if successor_visible:
                _record_rejection(
                    rejected,
                    successor,
                    reason="failed_acquire",
                    incumbent_pair=pair,
                    capacity_constraint=None,
                )
            if pair_visible and successor_abort_policy == "soft_retain":
                add_existing(pair, current_associations[pair])
            elif pair_visible:
                _record_rejection(
                    rejected,
                    pair,
                    reason=category,
                    incumbent_pair=successor,
                    capacity_constraint=None,
                )
            continue

        if elapsed >= mbb_overlap_ticks_for(gs_id) or not pair_visible:
            drop_current_pairs.add(pair)
            completion_message = (
                f"MBB teardown completed for old pair {pair!r}; successor {successor!r} "
                f"remains scheduled; elapsed_ticks={elapsed}; old_pair_visible={pair_visible}"
            )
            allocation_events.append(
                _make_event(
                    category="teardown_completed",
                    pair=pair,
                    gs_id=gs_id,
                    gs_tenant_ids=gs_tenant_ids,
                    gs_reference_bodies=gs_reference_bodies,
                    message=completion_message,
                    successor_pair=successor,
                    policy_kind="handover_mode",
                    policy_name="mbb",
                )
            )
            emit_mbb_lifecycle_terminal(
                category="teardown_completed",
                old_pair=pair,
                successor_pair=successor,
                gs_id=gs_id,
                message=completion_message,
            )
            if not pair_visible:
                emit_incumbent_lost(
                    pair,
                    f"Scheduled MBB teardown pair {pair!r} lost physical visibility",
                )
            if pair_visible:
                _record_rejection(
                    rejected,
                    pair,
                    reason="replaced_by_successor",
                    incumbent_pair=successor,
                    capacity_constraint=None,
                )
            continue

        add_existing(pair, current_associations[pair])
        new_pending_teardowns[pair] = teardown

    # Steady-state continuity: visible current links not in teardown survive as
    # incumbents. They may still be displaced later by handover policy.
    for pair, indices in sorted(current_associations.items()):
        if pair in pending_teardowns or pair in drop_current_pairs:
            continue
        if pair not in visible_set:
            emit_incumbent_lost(
                pair,
                f"Scheduled incumbent {pair!r} lost physical visibility",
            )
            continue
        add_existing(pair, indices)

    # Fail-safe convergence cap. Typical ticks complete in one or two passes;
    # the cap prevents a logic bug from spinning forever.
    max_candidate_passes = max(
        1,
        len(candidates) + len(current_associations) + len(pending_teardowns) + 1,
    )
    for _pass_idx in range(max_candidate_passes):
        progress = False
        for candidate in candidates:
            if candidate.pair in new_associations or rejection_locked(candidate.pair):
                continue

            gs_id = candidate.gs_id
            sat_id = candidate.sat_id
            tc = gs_terminal_counts[gs_id]

            protected_pair = protected_mbb_pair_for_gs(gs_id)
            if protected_pair is not None:
                _record_rejection(
                    rejected,
                    candidate.pair,
                    reason="mbb_overlap_locked",
                    incumbent_pair=protected_pair,
                    capacity_constraint=f"{gs_id}.terminals",
                )
                continue

            sat_displacement: _Candidate | None = None
            if not satellite_has_capacity(candidate):
                sat_displacement = displaceable_sat_incumbent(candidate)
                if sat_displacement is None:
                    _record_rejection(
                        rejected,
                        candidate.pair,
                        reason="sat_capacity",
                        incumbent_pair=sat_capacity_blocker(candidate),
                        capacity_constraint=sat_capacity_constraint(candidate),
                    )
                    continue

            gs_displacement: _Candidate | None = None
            steady_count = len(steady_pairs_for_gs(gs_id))
            logical_room = steady_count < steady_limit(gs_id)
            physical_room = len(gs_occupied.get(gs_id, set())) < tc

            if not logical_room:
                incumbent = worst_steady_incumbent(gs_id)
                if incumbent is None:
                    _record_rejection(
                        rejected,
                        candidate.pair,
                        reason="gs_capacity",
                        incumbent_pair=None,
                        capacity_constraint=f"{gs_id}.terminals",
                    )
                    continue

                handover_policy = gs_handover_policies[gs_id]
                decision = evaluate_handover(
                    policy=handover_policy,
                    incumbent_score=incumbent.selection_score,
                    challenger_score=candidate.selection_score,
                    context=HandoverContext(
                        step=step,
                        gs_id=gs_id,
                        incumbent_pair=incumbent.pair,
                        challenger_pair=candidate.pair,
                        incumbent_visibility=incumbent.visibility,
                        challenger_visibility=candidate.visibility,
                        min_elevation_deg=gs_min_elevations[gs_id],
                    ),
                )
                if decision.action == "hold":
                    if decision.unscheduled_reason is None:
                        raise ValueError(
                            f"Handover policy {handover_policy.name!r} held {candidate.pair!r} "
                            "but did not provide an unscheduled reason"
                        )
                    _record_rejection(
                        rejected,
                        candidate.pair,
                        reason=decision.unscheduled_reason,
                        incumbent_pair=incumbent.pair,
                        capacity_constraint=None,
                    )
                    continue
                if decision.action != "displace":
                    raise ValueError(
                        f"Handover policy {handover_policy.name!r} returned unknown action "
                        f"{decision.action!r}"
                    )
                gs_displacement = incumbent
                if handover_mode_for(gs_id) == "mbb" and not physical_room:
                    _record_rejection(
                        rejected,
                        candidate.pair,
                        reason="bbm_no_spare",
                        incumbent_pair=incumbent.pair,
                        capacity_constraint=f"{gs_id}.terminals",
                    )
                    continue
            elif not physical_room:
                _record_rejection(
                    rejected,
                    candidate.pair,
                    reason="gs_capacity",
                    incumbent_pair=protected_pair,
                    capacity_constraint=f"{gs_id}.terminals",
                )
                continue

            if gs_displacement is not None and handover_mode_for(gs_id) == "bbm":
                remove_association(gs_displacement.pair)
                if gs_displacement.pair in visible_set:
                    _record_rejection(
                        rejected,
                        gs_displacement.pair,
                        reason="replaced_by_successor",
                        incumbent_pair=candidate.pair,
                        capacity_constraint=None,
                    )
                emit_bbm_gap(gs_displacement, candidate)
                progress = True

            if sat_displacement is not None:
                remove_association(sat_displacement.pair)
                _record_rejection(
                    rejected,
                    sat_displacement.pair,
                    reason="sat_capacity",
                    incumbent_pair=candidate.pair,
                    capacity_constraint=sat_capacity_constraint(candidate),
                )
                progress = True

            if not allocate_new(candidate):
                raise ValueError(
                    f"Allocator selected {candidate.pair!r} after capacity arbitration "
                    "but could not allocate endpoint terminal indices"
                )
            progress = True

            if gs_displacement is not None and handover_mode_for(gs_id) == "mbb":
                new_pending_teardowns[gs_displacement.pair] = MbbTeardown(
                    start_step=step,
                    successor_pair=candidate.pair,
                )
                allocation_events.append(
                    _make_event(
                        category="mbb_overlap_started",
                        pair=gs_displacement.pair,
                        gs_id=gs_id,
                        gs_tenant_ids=gs_tenant_ids,
                        gs_reference_bodies=gs_reference_bodies,
                        message=(
                            f"MBB overlap started for incumbent {gs_displacement.pair!r} "
                            f"with successor {candidate.pair!r}; "
                            f"overlap_ticks={mbb_overlap_ticks_for(gs_id)}"
                        ),
                        successor_pair=candidate.pair,
                        challenger_pair=candidate.pair,
                        policy_kind="handover_mode",
                        policy_name="mbb",
                    )
                )

        if not progress:
            break
    else:
        raise ValueError(
            "Ground allocator did not converge after deterministic capacity arbitration passes"
        )

    unscheduled: list[UnscheduledPair] = []
    for pair in sorted(visible_set - set(new_associations)):
        gs_id, sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
        rejection = rejected.get(pair)
        if rejection is None:
            candidate = candidate_by_pair.get(pair)
            if candidate is None:
                raise ValueError(
                    f"Visible unscheduled pair {pair!r} has no allocation candidate; "
                    "visibility and allocation inputs are inconsistent"
                )
            if not satellite_has_capacity(candidate):
                rejection = _Rejected(
                    reason="sat_capacity",
                    incumbent_pair=sat_capacity_blocker(candidate),
                    capacity_constraint=sat_capacity_constraint(candidate),
                )
            elif (
                len(steady_pairs_for_gs(gs_id)) >= steady_limit(gs_id)
                or len(gs_occupied.get(gs_id, set())) >= gs_terminal_counts[gs_id]
            ):
                incumbent = worst_steady_incumbent(gs_id)
                rejection = _Rejected(
                    reason="gs_capacity",
                    incumbent_pair=incumbent.pair if incumbent else None,
                    capacity_constraint=f"{gs_id}.terminals",
                )
            else:
                raise ValueError(
                    f"Allocator could not attribute rejection reason for {pair!r}. "
                    "This is an allocator logic bug; fix attribution rather than "
                    "emitting a generic reason."
                )
        unscheduled.append(
            UnscheduledPair(
                pair=pair,
                tenant_id=gs_tenant_ids[gs_id],
                reference_body=gs_reference_bodies[gs_id],
                unscheduled_reason=rejection.reason,
                incumbent_pair=rejection.incumbent_pair,
                capacity_constraint=rejection.capacity_constraint,
            )
        )

    return GroundAllocationResult(
        associations=new_associations,
        pending_teardowns=new_pending_teardowns,
        scheduled_pairs=frozenset(new_associations),
        unscheduled_pairs=tuple(unscheduled),
        policy_audit=policy_audit,
        allocation_events=tuple(allocation_events),
        lifecycle_events=tuple(lifecycle_events),
    )
