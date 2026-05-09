# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

from ome.visibility import GroundVisibility

MbbTeardownState = dict[tuple[str, str], tuple[int, tuple[str, str]]]


@dataclass(frozen=True)
class GroundAllocationResult:
    """Result of the ground allocation pass for one OME tick."""

    associations: dict[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    scheduled_pairs: frozenset[tuple[str, str]]


def _compute_pair_score(elevation_deg: float, policy: str) -> float:
    """Score a GS/satellite pair. Always positive, higher is better."""
    if policy == "highest-elevation":
        return elevation_deg
    if policy == "lowest-elevation":
        return 90.0 - elevation_deg
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
    sat_ground_terminals: Mapping[str, int],
    mbb_overlap_ticks: int,
    mbb_reserve: int,
) -> GroundAllocationResult:
    """Allocate ground links for one tick.

    The allocator preserves active links when still visible, supports
    hysteresis-aware replacement, and tracks make-before-break overlap state.
    It fails loudly for unknown policies via `_compute_pair_score`; policy
    misspellings must never silently change handover behavior.
    """
    gs_scheduled: dict[tuple[str, str], bool] = {}
    sat_capacity: dict[str, int] = dict(sat_ground_terminals)

    scored_pairs: list[tuple[int, float, str, str, float, int]] = []
    score_lookup: dict[tuple[str, str], tuple[float, int]] = {}
    for gs_id, visible_sats in visible_per_station.items():
        policy = gs_policies.get(gs_id, "highest-elevation")
        min_elev = gs_min_elevations.get(gs_id, 25.0)
        hyst = gs_hysteresis.get(gs_id, HysteresisParameters())
        priority = gs_service_priorities.get(gs_id, 10)

        for gv in visible_sats:
            score = _compute_pair_score(gv.elevation_deg, policy)
            pair = (min(gs_id, gv.sat_id), max(gs_id, gv.sat_id))

            if pair in current_associations:
                discount = _compute_effective_discount(gv.elevation_deg, min_elev, hyst)
                score *= discount

            sat_gnd_cap = sat_ground_terminals.get(gv.sat_id, 1)
            scored_pairs.append((priority, score, gs_id, gv.sat_id, gv.range_km, sat_gnd_cap))
            score_lookup[pair] = (score, priority)

    scored_pairs.sort(key=lambda x: (x[0], -x[1], x[5]))

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
    for pair, (start_tick, successor) in pending_teardowns.items():
        if pair not in current_associations:
            continue
        gs_id_td, sat_id_td = _ground_and_satellite_ids(pair, ground_station_ids)
        gs_idx, sat_idx = current_associations[pair]
        elapsed = step - start_tick

        if elapsed >= mbb_overlap_ticks or pair not in visible_set:
            gs_occupied.setdefault(gs_id_td, set()).discard(gs_idx)
            sat_gnd_occupied.setdefault(sat_id_td, set()).discard(sat_idx)
        else:
            valid_teardowns[pair] = (start_tick, successor)

    # Step B/C: merge continuing overlap and new candidates into one
    # deterministic walk. Overlap wins over brand-new candidates at equal
    # priority/score so MBB teardown state remains stable for its window.
    merged: list[tuple[int, int, float, tuple[str, str], str, int, tuple[str, str] | None]] = []
    for prio, score, gs_id, sat_id, _range_km, _cap in scored_pairs:
        pair = (min(gs_id, sat_id), max(gs_id, sat_id))
        if pair in new_associations:
            continue
        if pair in valid_teardowns:
            start_tick_td, successor_td = valid_teardowns[pair]
            merged.append((prio, 1, -score, pair, "overlap", start_tick_td, successor_td))
        else:
            merged.append((prio, 2, -score, pair, "new", 0, None))

    merged.sort()

    for prio, _rank, neg_score, pair, kind, start_tick_m, successor_m in merged:
        gs_id_m, sat_id_m = _ground_and_satellite_ids(pair, ground_station_ids)

        if kind == "overlap":
            gs_idx, sat_idx = current_associations[pair]
            if sat_capacity.get(sat_id_m, 0) > 0:
                new_associations[pair] = (gs_idx, sat_idx)
                sat_capacity[sat_id_m] -= 1
                if successor_m is None:
                    raise ValueError(f"MBB teardown {pair} is missing successor link")
                new_pending_teardowns[pair] = (start_tick_m, successor_m)
                gs_scheduled[pair] = True
            else:
                gs_occupied.setdefault(gs_id_m, set()).discard(gs_idx)
                sat_gnd_occupied.setdefault(sat_id_m, set()).discard(sat_idx)
        else:
            tc = gs_terminal_counts.get(gs_id_m, 1)
            gs_steady = sum(
                1
                for p in new_associations
                if _ground_and_satellite_ids(p, ground_station_ids)[0] == gs_id_m
                and p not in new_pending_teardowns
            )
            gs_physical = len(gs_occupied.get(gs_id_m, set()))
            logical_room = gs_steady < (tc - mbb_reserve)
            physical_room = gs_physical < tc

            if logical_room and physical_room and sat_capacity.get(sat_id_m, 0) > 0:
                gs_occ = gs_occupied.get(gs_id_m, set())
                sat_occ = sat_gnd_occupied.get(sat_id_m, set())
                sat_cap_total = sat_ground_terminals.get(sat_id_m, 1)
                gs_idx = next((i for i in range(tc) if i not in gs_occ), None)
                sat_idx = next((i for i in range(sat_cap_total) if i not in sat_occ), None)
                if gs_idx is not None and sat_idx is not None:
                    new_associations[pair] = (gs_idx, sat_idx)
                    gs_occupied.setdefault(gs_id_m, set()).add(gs_idx)
                    sat_gnd_occupied.setdefault(sat_id_m, set()).add(sat_idx)
                    sat_capacity[sat_id_m] -= 1
                    gs_scheduled[pair] = True

            elif not logical_room and physical_room and sat_capacity.get(sat_id_m, 0) > 0:
                worst_pair: tuple[str, str] | None = None
                worst_score = float("inf")
                worst_prio = 0
                for p in new_associations:
                    p_gs, _p_sat = _ground_and_satellite_ids(p, ground_station_ids)
                    if p_gs != gs_id_m or p in new_pending_teardowns:
                        continue
                    p_score, p_prio = score_lookup.get(p, (0.0, 10))
                    if p_score < worst_score:
                        worst_pair, worst_score, worst_prio = p, p_score, p_prio

                score = -neg_score
                if worst_pair is not None and score > worst_score and prio <= worst_prio:
                    gs_occ = gs_occupied.get(gs_id_m, set())
                    sat_occ = sat_gnd_occupied.get(sat_id_m, set())
                    sat_cap_total = sat_ground_terminals.get(sat_id_m, 1)
                    gs_idx = next((i for i in range(tc) if i not in gs_occ), None)
                    sat_idx = next((i for i in range(sat_cap_total) if i not in sat_occ), None)
                    if gs_idx is not None and sat_idx is not None:
                        new_associations[pair] = (gs_idx, sat_idx)
                        gs_occupied.setdefault(gs_id_m, set()).add(gs_idx)
                        sat_gnd_occupied.setdefault(sat_id_m, set()).add(sat_idx)
                        sat_capacity[sat_id_m] -= 1
                        new_pending_teardowns[worst_pair] = (step, pair)
                        gs_scheduled[pair] = True

    # Successor-aware abort check: if the replacement link did not survive the
    # allocation walk, the old link cannot remain in teardown state.
    for pair in list(new_pending_teardowns.keys()):
        _start, successor = new_pending_teardowns[pair]
        if successor not in new_associations or successor in new_pending_teardowns:
            del new_pending_teardowns[pair]

    # Mark every allocated link scheduled even if it reached the output via
    # continuity rather than the candidate walk.
    for pair in new_associations:
        if pair not in gs_scheduled:
            gs_scheduled[pair] = True

    return GroundAllocationResult(
        associations=new_associations,
        pending_teardowns=new_pending_teardowns,
        scheduled_pairs=frozenset(pair for pair, scheduled in gs_scheduled.items() if scheduled),
    )
