# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Prove deterministic dispatch and allocation ordering.

Candidate input order must not change the selected winner. Scheduler checks
exercise authority freshness and dispatch ordering separately.
"""

from __future__ import annotations

import pytest
from nodalarc.models.ground_policy import (
    HandoverPolicySpec,
    HysteresisParameters,
    SelectionPolicySpec,
)
from ome.ground_allocator import allocate_ground_links
from ome.visibility import GroundVisibility


def _policy_kwargs(gs_id: str) -> dict:
    return {
        "gs_selection_policies": {gs_id: SelectionPolicySpec(name="highest-elevation")},
        "gs_handover_policies": {
            gs_id: HandoverPolicySpec(name="hysteresis", params=HysteresisParameters().model_dump())
        },
        "gs_handover_modes": {gs_id: "bbm"},
        "gs_mbb_overlap_ticks": {gs_id: 0},
        "gs_mbb_reserve": {gs_id: 0},
        "ranking_order": ("service_priority", "selection_score", "lex_pair"),
        "mbb_preemption": "off",
        "successor_abort_policy": "hard_release",
        "cross_tenant_displacement": "off",
        "bbm_acquire_timeout_ticks": 1,
        "ignored_capacity_fields": (),
    }


def _sat_body_pools(sat_terminals: dict[str, int]) -> dict[str, dict[str, tuple[int, ...]]]:
    return {sat_id: {"earth": tuple(range(count))} for sat_id, count in sat_terminals.items()}


class TestGroundAllocatorDeterminism:
    """The OME ground allocator sort must resolve all ties deterministically."""

    @pytest.mark.parametrize("reverse", [False, True], ids=["ascending", "descending"])
    def test_tiebreaker_selects_lexicographically_first_pair(self, reverse):
        """When priority, score, and sat capacity are equal, the allocator
        must select the pair with the lexicographically smaller (gs_id, sat_id).
        """
        gs_id = "gs-A"
        sat_a = "sat-P00S00"
        sat_b = "sat-P01S00"

        visible = [
            GroundVisibility(
                sat_id=sat_b,
                visible=True,
                elevation_deg=45.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id=sat_a,
                visible=True,
                elevation_deg=45.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ]

        visible.sort(key=lambda candidate: candidate.sat_id, reverse=reverse)
        result = allocate_ground_links(
            step=0,
            visible_per_station={gs_id: visible},
            ground_station_ids={gs_id},
            current_associations={},
            pending_teardowns={},
            gs_terminal_indices={gs_id: (0,)},
            sat_ground_terminals={sat_a: 1, sat_b: 1},
            sat_ground_terminal_indices_by_body=_sat_body_pools({sat_a: 1, sat_b: 1}),
            **_policy_kwargs(gs_id),
            gs_min_elevations={gs_id: 25.0},
            gs_service_priorities={gs_id: 10},
            gs_tenant_ids={gs_id: "default"},
            gs_reference_bodies={gs_id: "earth"},
        )

        # (gs-A, sat-P00S00) < (gs-A, sat-P01S00) lexicographically
        expected_pair = (min(gs_id, sat_a), max(gs_id, sat_a))
        assert result.scheduled_pairs == frozenset({expected_pair})
        assert result.associations == {expected_pair: (0, 0)}
