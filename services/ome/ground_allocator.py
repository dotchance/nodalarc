# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-link allocation engine.

This module owns the policy and terminal-index state for ground handover.
It deliberately does not propagate satellites or evaluate visibility. Its
input is the set of physically visible GS/satellite candidates for one OME
tick; its output is the allocation state that event generation and snapshots
can audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.models.ground_station import HysteresisParameters
from nodalarc.models.link_decisions import GroundUnscheduledReason, UnscheduledPair

from ome.types import MbbTeardown, MbbTeardownState
from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class GroundAllocationResult:
    """Result of the ground allocation pass for one OME tick.

    ``unscheduled_pairs`` carries one entry per visible pair that the
    allocator considered but did not schedule, with the attributed
    reason. Active MBB teardown pairs (scheduled, draining) are NOT in
    ``unscheduled_pairs`` — they remain in ``associations`` and
    ``scheduled_pairs`` with ``scheduling_state="teardown"`` on the
    event/snapshot. Only the post-teardown released pair appears here
    (as ``replaced_by_successor``).
    """

    associations: dict[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    scheduled_pairs: frozenset[tuple[str, str]]
    unscheduled_pairs: tuple[UnscheduledPair, ...]


def _compute_pair_score(
    elevation_deg: float,
    policy: str,
    remaining_visible_s: float | None = None,
) -> float:
    """Score a GS/satellite pair. Always positive, higher is better."""
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
    """Compute the hysteresis discount factor with mask-edge fade.

    INVARIANT: elevation_deg is the raw physical elevation, never a
    policy-adjusted score. The fade is a geometric property of the link's
    proximity to the elevation mask.
    """
    fade_bottom = min_elevation_deg
    fade_top = min_elevation_deg + hyst.mask_fade_range_deg
    if elevation_deg <= fade_bottom:
        return 1.0
    if elevation_deg >= fade_top:
        return hyst.discount_factor
    t = (elevation_deg - fade_bottom) / hyst.mask_fade_range_deg
    return 1.0 + (hyst.discount_factor - 1.0) * t


def _ground_and_satellite_ids(
    pair: tuple[str, str],
    ground_station_ids: set[str],
) -> tuple[str, str]:
    """Return `(ground_station_id, satellite_id)` for a normalized pair."""
    if pair[0] in ground_station_ids:
        return pair[0], pair[1]
    return pair[1], pair[0]


def _find_gs_incumbent(
    new_associations: Mapping[tuple[str, str], tuple[int, int]],
    new_pending_teardowns: Mapping[tuple[str, str], MbbTeardown],
    ground_station_ids: set[str],
    gs_id: str,
) -> tuple[str, str] | None:
    """Return one steady-state (non-teardown) incumbent on the given GS, if any.

    Used to attribute `hysteresis_hold` and `incumbent_held` rejections — the
    operator should see WHICH incumbent the candidate lost to.
    """
    for ap in sorted(new_associations):
        if ap in new_pending_teardowns:
            continue
        ap_gs, _ = _ground_and_satellite_ids(ap, ground_station_ids)
        if ap_gs == gs_id:
            return ap
    return None


def _find_sat_incumbent(
    new_associations: Mapping[tuple[str, str], tuple[int, int]],
    ground_station_ids: set[str],
    sat_id: str,
) -> tuple[str, str] | None:
    """Return one association that consumed the given sat's ground-terminal capacity, if any."""
    for ap in sorted(new_associations):
        _, ap_sat = _ground_and_satellite_ids(ap, ground_station_ids)
        if ap_sat == sat_id:
            return ap
    return None


def _attribute_rejected_pairs(
    *,
    visible_set: set[tuple[str, str]],
    new_associations: Mapping[tuple[str, str], tuple[int, int]],
    new_pending_teardowns: Mapping[tuple[str, str], MbbTeardown],
    pending_teardowns: MbbTeardownState,
    gs_occupied: Mapping[str, set[int]],
    sat_capacity: Mapping[str, int],
    gs_terminal_counts: Mapping[str, int],
    ground_station_ids: set[str],
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    mbb_reserve: int,
    mbb_overlap_ticks: int,
    step: int,
) -> tuple[UnscheduledPair, ...]:
    """Attribute a typed rejection reason to every visible pair that did not allocate.

    The attribution inspects the post-allocation state of the allocator
    (which terminals are occupied, what survived the candidate walk) and
    picks the reason that best fits each rejected pair.

    Returns a tuple sorted by pair for deterministic NATS payloads —
    Direction 4 (multi-compute-node) requires that two Scheduler replicas
    receiving the same `GroundLinkDecisionSnapshot` see the same ordering.

    The final `else` branch raises rather than returning a default. The
    plan demands no silent fall-throughs: if the allocator cannot
    attribute a rejection to one of the typed reasons, the producer is
    wrong and must fail loud so we can fix the attribution logic, not
    hide it behind a generic value.
    """
    rejected = sorted(visible_set - set(new_associations.keys()))
    out: list[UnscheduledPair] = []

    for pair in rejected:
        gs_id, sat_id = _ground_and_satellite_ids(pair, ground_station_ids)
        tenant_id = gs_tenant_ids[gs_id]
        reference_body = gs_reference_bodies[gs_id]
        reason: GroundUnscheduledReason

        # Case 1: this pair was in an MBB teardown that just expired.
        # The OME has released the slot for a successor. The pair is
        # still visible but no longer allocated.
        if pair in pending_teardowns:
            teardown = pending_teardowns[pair]
            elapsed = step - teardown.start_step
            if elapsed >= mbb_overlap_ticks:
                out.append(
                    UnscheduledPair(
                        pair=pair,
                        tenant_id=tenant_id,
                        reference_body=reference_body,
                        unscheduled_reason="replaced_by_successor",
                        incumbent_pair=teardown.successor_pair,
                        capacity_constraint=None,
                    )
                )
                continue

        # Case 2: the satellite's ground-terminal capacity is exhausted
        # globally. A co-located ground station got there first.
        if sat_capacity.get(sat_id, 0) <= 0:
            incumbent = _find_sat_incumbent(new_associations, ground_station_ids, sat_id)
            out.append(
                UnscheduledPair(
                    pair=pair,
                    tenant_id=tenant_id,
                    reference_body=reference_body,
                    unscheduled_reason="sat_capacity",
                    incumbent_pair=incumbent,
                    capacity_constraint=f"{sat_id}.ground_terminals",
                )
            )
            continue

        # Case 3: GS-side resource constraint.
        tc = gs_terminal_counts[gs_id]
        gs_physical = len(gs_occupied.get(gs_id, set()))
        gs_steady = sum(
            1
            for ap in new_associations
            if _ground_and_satellite_ids(ap, ground_station_ids)[0] == gs_id
            and ap not in new_pending_teardowns
        )
        logical_room = gs_steady < (tc - mbb_reserve)
        physical_room = gs_physical < tc
        incumbent = _find_gs_incumbent(
            new_associations, new_pending_teardowns, ground_station_ids, gs_id
        )

        if not physical_room and tc == 1:
            # Single-terminal GS with an incumbent occupying the only slot.
            # The allocator has no BBM-displacement path for this case
            # (verified in the foundations plan as Finding 2).
            reason = "bbm_no_spare"
        elif not logical_room and physical_room:
            # Displacement was eligible (physical_room true, no logical
            # room) but the displacement check did not promote this
            # candidate. The incumbent's discount-boosted score won, or
            # priorities did not allow the swap. Phase 1.3 attributes
            # both as `hysteresis_hold` — Phase 3 of the foundations
            # plan refines the selection-vs-handover split, at which
            # point we will distinguish `hysteresis_hold` from
            # `incumbent_held` more precisely.
            reason = "hysteresis_hold"
        elif not physical_room:
            # Multi-terminal GS with all physical terminals occupied
            # (steady + overlap). No room to add even with logical-room
            # allowance.
            reason = "gs_capacity"
        else:
            # sat has capacity, GS has both logical and physical room —
            # the allocator should have scheduled this pair. The merge
            # loop missed it. This is a logic bug we want to surface.
            raise ValueError(
                f"Allocator could not attribute rejection reason for {pair} — "
                f"sat_capacity[{sat_id}]={sat_capacity.get(sat_id, 0)} > 0, "
                f"gs_steady={gs_steady}, gs_physical={gs_physical}, tc={tc}, "
                f"mbb_reserve={mbb_reserve}. This is an allocator logic bug; "
                "fix the attribution rather than hiding it behind a default."
            )

        out.append(
            UnscheduledPair(
                pair=pair,
                tenant_id=tenant_id,
                reference_body=reference_body,
                unscheduled_reason=reason,
                incumbent_pair=incumbent,
                capacity_constraint=f"{gs_id}.terminals" if reason != "hysteresis_hold" else None,
            )
        )

    return tuple(out)


def allocate_ground_links(
    *,
    step: int,
    visible_per_station: Mapping[str, list[GroundVisibility]],
    ground_station_ids: set[str],
    current_associations: Mapping[tuple[str, str], tuple[int, int]],
    pending_teardowns: MbbTeardownState,
    gs_terminal_counts: Mapping[str, int],
    gs_policies: Mapping[str, str],
    gs_min_elevations: Mapping[str, float],
    gs_hysteresis: Mapping[str, HysteresisParameters],
    gs_service_priorities: Mapping[str, int],
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    sat_ground_terminals: Mapping[str, int],
    mbb_overlap_ticks: int,
    mbb_reserve: int,
) -> GroundAllocationResult:
    """Allocate ground links for one tick.

    The allocator preserves active links when still visible, supports
    hysteresis-aware replacement, and tracks make-before-break overlap state.
    It fails loudly for unknown policies via `_compute_pair_score`; policy
    misspellings must never silently change handover behavior.

    Direction 2 (multi-tenant) and Direction 3 (multi-body) require every
    unscheduled-pair record to carry tenant scope and body anchor. Missing
    per-GS ``tenant_id`` or ``reference_body`` is fatal at the producer
    boundary — no silent fall-through to a default.
    """
    missing_tenant = sorted(set(visible_per_station) - set(gs_tenant_ids))
    if missing_tenant:
        raise ValueError(
            "Ground allocator is missing tenant_id for "
            f"{', '.join(missing_tenant)} — Direction 2 requires every "
            "unscheduled-pair record to carry tenant scope"
        )
    missing_body = sorted(set(visible_per_station) - set(gs_reference_bodies))
    if missing_body:
        raise ValueError(
            "Ground allocator is missing reference_body for "
            f"{', '.join(missing_body)} — Direction 3 requires every "
            "unscheduled-pair record to be anchored to a specific body"
        )
    gs_scheduled: dict[tuple[str, str], bool] = {}
    sat_capacity: dict[str, int] = dict(sat_ground_terminals)

    scored_pairs: list[tuple[int, float, str, str, float, int]] = []
    score_lookup: dict[tuple[str, str], tuple[float, int]] = {}
    for gs_id, visible_sats in visible_per_station.items():
        policy = gs_policies[gs_id]
        min_elev = gs_min_elevations[gs_id]
        hyst = gs_hysteresis[gs_id]
        priority = gs_service_priorities[gs_id]

        for gv in visible_sats:
            score = _compute_pair_score(gv.elevation_deg, policy, gv.remaining_visible_s)
            pair = (min(gs_id, gv.sat_id), max(gs_id, gv.sat_id))

            if pair in current_associations:
                discount = _compute_effective_discount(gv.elevation_deg, min_elev, hyst)
                score *= discount

            sat_gnd_cap = sat_ground_terminals[gv.sat_id]
            scored_pairs.append((priority, score, gs_id, gv.sat_id, gv.range_km, sat_gnd_cap))
            score_lookup[pair] = (score, priority)

    # Final tiebreaker (gs_id, sat_id) ensures deterministic allocation when
    # priority, score, and satellite capacity are equal. Without this, dict
    # iteration order from upstream (hash-seed-dependent in older Pythons,
    # insertion-order in 3.7+ but still input-dependent) decides which pair
    # wins a contested terminal — producing different VisibilityEvent sequences
    # from identical session configs.
    scored_pairs.sort(key=lambda x: (x[0], -x[1], x[5], x[2], x[3]))

    visible_set: set[tuple[str, str]] = {
        (min(gs, sat), max(gs, sat)) for _, _, gs, sat, _, _ in scored_pairs
    }

    # Physical occupancy is derived from all current associations, including
    # links that may be released later in this tick. This preserves terminal
    # exclusivity during MBB overlap.
    gs_occupied: dict[str, set[int]] = {}
    sat_gnd_occupied: dict[str, set[int]] = {}
    for pair, (gs_idx, sat_idx) in current_associations.items():
        gs_id_ca, sat_id_ca = _ground_and_satellite_ids(pair, ground_station_ids)
        gs_occupied.setdefault(gs_id_ca, set()).add(gs_idx)
        sat_gnd_occupied.setdefault(sat_id_ca, set()).add(sat_idx)

    new_associations: dict[tuple[str, str], tuple[int, int]] = {}
    new_pending_teardowns: MbbTeardownState = {}

    # Step A: steady-state continuity. Existing visible non-teardown links keep
    # their physical terminal indices before new candidates compete.
    for pair, (gs_idx, sat_idx) in current_associations.items():
        gs_id_a, sat_id_a = _ground_and_satellite_ids(pair, ground_station_ids)

        if pair in pending_teardowns:
            continue
        if pair not in visible_set:
            gs_occupied.setdefault(gs_id_a, set()).discard(gs_idx)
            sat_gnd_occupied.setdefault(sat_id_a, set()).discard(sat_idx)
            continue

        new_associations[pair] = (gs_idx, sat_idx)
        sat_capacity[sat_id_a] -= 1
        gs_scheduled[pair] = True

    # Expire/free MBB teardown occupancy before allocating new or overlapping
    # links. Remaining teardown entries still consume physical terminals.
    valid_teardowns: MbbTeardownState = {}
    for pair, teardown in pending_teardowns.items():
        if pair not in current_associations:
            continue
        gs_id_td, sat_id_td = _ground_and_satellite_ids(pair, ground_station_ids)
        gs_idx, sat_idx = current_associations[pair]
        elapsed = step - teardown.start_step

        if elapsed >= mbb_overlap_ticks or pair not in visible_set:
            gs_occupied.setdefault(gs_id_td, set()).discard(gs_idx)
            sat_gnd_occupied.setdefault(sat_id_td, set()).discard(sat_idx)
        else:
            valid_teardowns[pair] = teardown

    # Step B/C: merge continuing overlap and new candidates into one
    # deterministic walk. Overlap wins over brand-new candidates at equal
    # priority/score so MBB teardown state remains stable for its window.
    merged: list[tuple[int, int, float, tuple[str, str], str, int, tuple[str, str] | None]] = []
    for prio, score, gs_id, sat_id, _range_km, _cap in scored_pairs:
        pair = (min(gs_id, sat_id), max(gs_id, sat_id))
        if pair in new_associations:
            continue
        if pair in valid_teardowns:
            teardown = valid_teardowns[pair]
            merged.append(
                (prio, 1, -score, pair, "overlap", teardown.start_step, teardown.successor_pair)
            )
        else:
            merged.append((prio, 2, -score, pair, "new", 0, None))

    merged.sort()

    for prio, _rank, neg_score, pair, kind, start_tick_m, successor_m in merged:
        gs_id_m, sat_id_m = _ground_and_satellite_ids(pair, ground_station_ids)

        if kind == "overlap":
            gs_idx, sat_idx = current_associations[pair]
            if sat_capacity[sat_id_m] > 0:
                new_associations[pair] = (gs_idx, sat_idx)
                sat_capacity[sat_id_m] -= 1
                if successor_m is None:
                    raise ValueError(f"MBB teardown {pair} is missing successor link")
                new_pending_teardowns[pair] = MbbTeardown(
                    start_step=start_tick_m,
                    successor_pair=successor_m,
                )
                gs_scheduled[pair] = True
            else:
                gs_occupied.setdefault(gs_id_m, set()).discard(gs_idx)
                sat_gnd_occupied.setdefault(sat_id_m, set()).discard(sat_idx)
        else:
            tc = gs_terminal_counts[gs_id_m]
            gs_steady = sum(
                1
                for p in new_associations
                if _ground_and_satellite_ids(p, ground_station_ids)[0] == gs_id_m
                and p not in new_pending_teardowns
            )
            gs_physical = len(gs_occupied.get(gs_id_m, set()))
            logical_room = gs_steady < (tc - mbb_reserve)
            physical_room = gs_physical < tc

            if logical_room and physical_room and sat_capacity[sat_id_m] > 0:
                gs_occ = gs_occupied.get(gs_id_m, set())
                sat_occ = sat_gnd_occupied.get(sat_id_m, set())
                sat_cap_total = sat_ground_terminals[sat_id_m]
                gs_idx = next((i for i in range(tc) if i not in gs_occ), None)
                sat_idx = next((i for i in range(sat_cap_total) if i not in sat_occ), None)
                if gs_idx is not None and sat_idx is not None:
                    new_associations[pair] = (gs_idx, sat_idx)
                    gs_occupied.setdefault(gs_id_m, set()).add(gs_idx)
                    sat_gnd_occupied.setdefault(sat_id_m, set()).add(sat_idx)
                    sat_capacity[sat_id_m] -= 1
                    gs_scheduled[pair] = True

            elif not logical_room and physical_room and sat_capacity[sat_id_m] > 0:
                worst_pair: tuple[str, str] | None = None
                worst_score = float("inf")
                worst_prio = 0
                for p in new_associations:
                    p_gs, _p_sat = _ground_and_satellite_ids(p, ground_station_ids)
                    if p_gs != gs_id_m or p in new_pending_teardowns:
                        continue
                    p_score, p_prio = score_lookup[p]
                    if p_score < worst_score:
                        worst_pair, worst_score, worst_prio = p, p_score, p_prio

                score = -neg_score
                if worst_pair is not None and score > worst_score and prio <= worst_prio:
                    gs_occ = gs_occupied.get(gs_id_m, set())
                    sat_occ = sat_gnd_occupied.get(sat_id_m, set())
                    sat_cap_total = sat_ground_terminals[sat_id_m]
                    gs_idx = next((i for i in range(tc) if i not in gs_occ), None)
                    sat_idx = next((i for i in range(sat_cap_total) if i not in sat_occ), None)
                    if gs_idx is not None and sat_idx is not None:
                        new_associations[pair] = (gs_idx, sat_idx)
                        gs_occupied.setdefault(gs_id_m, set()).add(gs_idx)
                        sat_gnd_occupied.setdefault(sat_id_m, set()).add(sat_idx)
                        sat_capacity[sat_id_m] -= 1
                        new_pending_teardowns[worst_pair] = MbbTeardown(
                            start_step=step,
                            successor_pair=pair,
                        )
                        gs_scheduled[pair] = True

    # Successor-aware abort check: if the replacement link did not survive the
    # allocation walk, the old link cannot remain in teardown state.
    for pair in list(new_pending_teardowns.keys()):
        successor = new_pending_teardowns[pair].successor_pair
        if successor not in new_associations or successor in new_pending_teardowns:
            del new_pending_teardowns[pair]

    # Mark every allocated link scheduled even if it reached the output via
    # continuity rather than the candidate walk.
    for pair in new_associations:
        if pair not in gs_scheduled:
            gs_scheduled[pair] = True

    # Attribute the rejection reason for every visible pair that did not
    # end up scheduled. The attribution is end-of-allocation: it inspects
    # the FINAL state (which terminals were occupied, what survived the
    # candidate walk) and assigns a typed reason. Missing attribution is
    # a fatal logic bug — see the final `else` of `_attribute_rejection`.
    unscheduled_list = _attribute_rejected_pairs(
        visible_set=visible_set,
        new_associations=new_associations,
        new_pending_teardowns=new_pending_teardowns,
        pending_teardowns=pending_teardowns,
        gs_occupied=gs_occupied,
        sat_capacity=sat_capacity,
        gs_terminal_counts=gs_terminal_counts,
        ground_station_ids=ground_station_ids,
        gs_tenant_ids=gs_tenant_ids,
        gs_reference_bodies=gs_reference_bodies,
        mbb_reserve=mbb_reserve,
        mbb_overlap_ticks=mbb_overlap_ticks,
        step=step,
    )

    return GroundAllocationResult(
        associations=new_associations,
        pending_teardowns=new_pending_teardowns,
        scheduled_pairs=frozenset(pair for pair, scheduled in gs_scheduled.items() if scheduled),
        unscheduled_pairs=unscheduled_list,
    )
