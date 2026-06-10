# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Pure Scheduler dispatch planning helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scheduler.desired_state import ActiveLinkInfo


@dataclass(frozen=True)
class ReconcileDiff:
    """Set-level difference between actual and desired link state."""

    to_remove: set[tuple[str, str]]
    to_add: set[tuple[str, str]]
    to_update_latency: set[tuple[str, str]]

    @property
    def has_changes(self) -> bool:
        return bool(self.to_remove or self.to_add or self.to_update_latency)


@dataclass(frozen=True)
class MbbClassification:
    """Ground/ISL classification and segment mode for one reconcile pass."""

    isl_downs: set[tuple[str, str]]
    isl_ups: set[tuple[str, str]]
    gs_downs: dict[str, set[tuple[str, str]]]
    gs_ups: dict[str, set[tuple[str, str]]]
    mbb_segments: set[str]
    bbm_segments: set[str]


def diff_link_state(
    actual: Mapping[tuple[str, str], ActiveLinkInfo],
    desired: Mapping[tuple[str, str], ActiveLinkInfo],
    *,
    latency_tolerance_ms: float = 1e-9,
) -> ReconcileDiff:
    """Compute add/remove/latency-update sets for a reconcile pass."""
    current_pairs = set(actual.keys())
    desired_pairs = set(desired.keys())
    common_pairs = current_pairs & desired_pairs
    return ReconcileDiff(
        to_remove=current_pairs - desired_pairs,
        to_add=desired_pairs - current_pairs,
        to_update_latency={
            pair
            for pair in common_pairs
            if (
                abs(actual[pair].latency_ms - desired[pair].latency_ms) > latency_tolerance_ms
                or actual[pair].range_km != desired[pair].range_km
            )
        },
    )


def gs_id_for_pair(pair: tuple[str, str], gs_capacities: Mapping[str, int]) -> str | None:
    if pair[0] in gs_capacities:
        return pair[0]
    if pair[1] in gs_capacities:
        return pair[1]
    return None


def sat_id_for_gs_pair(pair: tuple[str, str], gs_capacities: Mapping[str, int]) -> str | None:
    if pair[0] in gs_capacities:
        return pair[1]
    if pair[1] in gs_capacities:
        return pair[0]
    return None


def interface_colliding_downs(
    *,
    to_remove: set[tuple[str, str]],
    to_add: set[tuple[str, str]],
    actual: Mapping[tuple[str, str], ActiveLinkInfo],
    desired: Mapping[tuple[str, str], ActiveLinkInfo],
) -> set[tuple[str, str]]:
    """Release pairs whose kernel interface is reused by a same-pass acquire.

    The OME allocator may hand a terminal freed in one tick straight to the
    successor link in the same tick. Such a handover cannot be
    make-before-break: one kernel interface cannot carry the old and the new
    link at once, and dispatching the acquire first means the deferred
    release tears down the interface state the acquire just created. Every
    release returned here must be dispatched before the acquires.
    """
    acquire_endpoints: set[tuple[str, str]] = set()
    for pair in to_add:
        info = desired[pair]
        acquire_endpoints.add((pair[0], info.interface_a))
        acquire_endpoints.add((pair[1], info.interface_b))

    colliding: set[tuple[str, str]] = set()
    for pair in to_remove:
        info = actual[pair]
        if (pair[0], info.interface_a) in acquire_endpoints or (
            pair[1],
            info.interface_b,
        ) in acquire_endpoints:
            colliding.add(pair)
    return colliding


def classify_mbb_changes(
    *,
    to_remove: set[tuple[str, str]],
    to_add: set[tuple[str, str]],
    gs_capacities: Mapping[str, int],
    gs_active_count: Mapping[str, int],
    sat_capacities: Mapping[str, int],
    sat_active_count: Mapping[str, int],
    forced_bbm_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> MbbClassification:
    """Classify one reconcile diff into ISL operations and GS MBB/BBM segments."""
    isl_downs: set[tuple[str, str]] = set()
    isl_ups: set[tuple[str, str]] = set()
    gs_downs: dict[str, set[tuple[str, str]]] = {}
    gs_ups: dict[str, set[tuple[str, str]]] = {}

    for pair in to_remove:
        gs_id = gs_id_for_pair(pair, gs_capacities)
        if gs_id:
            gs_downs.setdefault(gs_id, set()).add(pair)
        else:
            isl_downs.add(pair)

    for pair in to_add:
        gs_id = gs_id_for_pair(pair, gs_capacities)
        if gs_id:
            gs_ups.setdefault(gs_id, set()).add(pair)
        else:
            isl_ups.add(pair)

    dirty_gs = set(gs_downs) | set(gs_ups)
    mbb_segments: set[str] = set()
    bbm_segments: set[str] = set()

    for gs_id in dirty_gs:
        segment_pairs = gs_downs.get(gs_id, set()) | gs_ups.get(gs_id, set())
        if segment_pairs & forced_bbm_pairs:
            bbm_segments.add(gs_id)
            continue

        ups = gs_ups.get(gs_id, set())
        if not ups:
            bbm_segments.add(gs_id)
            continue

        downs = gs_downs.get(gs_id, set())
        gs_spare = gs_capacities[gs_id] - gs_active_count.get(gs_id, 0)
        all_sats_ok = True
        for pair in ups:
            sat_id = sat_id_for_gs_pair(pair, gs_capacities)
            if sat_id is None:
                raise ValueError(f"Ground segment {gs_id!r} includes non-ground pair {pair}")
            if sat_capacities[sat_id] - sat_active_count.get(sat_id, 0) <= 0:
                all_sats_ok = False
                break

        if not downs:
            if gs_spare >= len(ups) and all_sats_ok:
                mbb_segments.add(gs_id)
            else:
                bbm_segments.add(gs_id)
            continue

        if gs_spare >= len(ups) and all_sats_ok:
            mbb_segments.add(gs_id)
        else:
            bbm_segments.add(gs_id)

    return MbbClassification(
        isl_downs=isl_downs,
        isl_ups=isl_ups,
        gs_downs=gs_downs,
        gs_ups=gs_ups,
        mbb_segments=mbb_segments,
        bbm_segments=bbm_segments,
    )
