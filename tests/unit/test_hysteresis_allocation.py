# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for hysteresis-aware ground link allocation.

The helper-function tests pin score/discount invariants; the allocator tests
exercise real make-before-break replacement decisions so production ordering,
terminal occupancy, and pending teardown behavior are covered together.
"""

from __future__ import annotations

import pytest
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import (
    _compute_effective_discount,
    _compute_pair_score,
    allocate_ground_links,
)
from ome.types import MbbTeardown
from ome.visibility import GroundVisibility


def _selection_policy(name: str) -> SelectionPolicySpec:
    params = {"lookahead_horizon_ticks": 600} if name == "longest-remaining-pass" else {}
    return SelectionPolicySpec(name=name, params=params)


def _handover_policy() -> HandoverPolicySpec:
    return HandoverPolicySpec(
        name="hysteresis",
        params=HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0).model_dump(),
    )


def _policy_kwargs(policy: str, handover_mode: str) -> dict:
    return {
        "gs_selection_policies": {"gs-test": _selection_policy(policy)},
        "gs_handover_policies": {"gs-test": _handover_policy()},
        "ranking_order": ("service_priority", "selection_score", "lex_pair"),
        "handover_mode": handover_mode,
        "mbb_preemption": "off",
        "successor_abort_policy": "hard_release",
        "cross_tenant_displacement": "off",
        "bbm_acquire_timeout_ticks": 1,
        "ignored_capacity_fields": (),
    }


def _sat_body_pools(sat_terminals: dict[str, int]) -> dict[str, dict[str, tuple[int, ...]]]:
    return {sat_id: {"earth": tuple(range(count))} for sat_id, count in sat_terminals.items()}


def _visible_gv(sat_id: str, elevation_deg: float, range_km: float) -> GroundVisibility:
    """Build a visible GroundVisibility for allocator-input fixtures.

    All fields explicit — no defaults, no fallbacks. Tests that need
    invisible pairs construct GroundVisibility directly with the
    appropriate reject_reason.
    """
    return GroundVisibility(
        sat_id=sat_id,
        visible=True,
        elevation_deg=elevation_deg,
        range_km=range_km,
        remaining_visible_s=None,
        reject_reason="ok",
    )


class TestComputePairScore:
    """_compute_pair_score must always return positive, higher=better."""

    def test_highest_elevation_positive(self):
        assert _compute_pair_score(45.0, "highest-elevation") == 45.0

    def test_lowest_elevation_positive(self):
        score = _compute_pair_score(45.0, "lowest-elevation")
        assert score == 45.0  # 90 - 45

    def test_lowest_elevation_lower_elev_higher_score(self):
        s30 = _compute_pair_score(30.0, "lowest-elevation")
        s45 = _compute_pair_score(45.0, "lowest-elevation")
        assert s30 > s45  # 60 > 45

    def test_highest_elevation_higher_elev_higher_score(self):
        s30 = _compute_pair_score(30.0, "highest-elevation")
        s45 = _compute_pair_score(45.0, "highest-elevation")
        assert s45 > s30

    def test_longest_remaining_pass_uses_dwell_time(self):
        s_short = _compute_pair_score(70.0, "longest-remaining-pass", 12.0)
        s_long = _compute_pair_score(30.0, "longest-remaining-pass", 90.0)
        assert s_long > s_short

    def test_longest_remaining_pass_requires_lookahead_value(self):
        with pytest.raises(ValueError, match="requires OME pass lookahead"):
            _compute_pair_score(50.0, "longest-remaining-pass")

    def test_unknown_policy_fails_loudly(self):
        with pytest.raises(ValueError, match="Unknown ground scheduling policy"):
            _compute_pair_score(50.0, "unknown")


class TestComputeEffectiveDiscount:
    """_compute_effective_discount uses raw physical elevation for fade."""

    def test_below_mask_no_discount(self):
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        assert _compute_effective_discount(24.0, 25.0, hyst) == 1.0

    def test_at_mask_no_discount(self):
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        assert _compute_effective_discount(25.0, 25.0, hyst) == 1.0

    def test_above_fade_full_discount(self):
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        assert _compute_effective_discount(30.0, 25.0, hyst) == 1.15

    def test_well_above_fade_full_discount(self):
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        assert _compute_effective_discount(60.0, 25.0, hyst) == 1.15

    def test_mid_fade_partial_discount(self):
        # min_elev=25, fade_range=5 → fade zone [25, 30]
        # elevation=27 → t = (27-25)/5 = 0.4
        # discount = 1.0 + 0.4 * 0.15 = 1.06
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        d = _compute_effective_discount(27.0, 25.0, hyst)
        assert abs(d - 1.06) < 1e-10

    def test_fade_top_boundary(self):
        # elevation exactly at fade_top → full discount
        hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
        d = _compute_effective_discount(30.0, 25.0, hyst)
        assert d == 1.15


class TestHysteresisDiscount:
    """Hysteresis must affect real allocator replacement decisions."""

    def _allocate(
        self,
        visible: list[GroundVisibility],
        *,
        current: dict[tuple[str, str], tuple[int, int]] | None = None,
        policy: str = "highest-elevation",
        gs_terminals: int = 2,
        min_elev: float = 25.0,
        mbb_reserve: int = 1,
    ):
        sat_terminals = {gv.sat_id: 1 for gv in visible}
        return allocate_ground_links(
            step=10,
            visible_per_station={"gs-test": visible},
            ground_station_ids={"gs-test"},
            current_associations=current or {},
            pending_teardowns={},
            gs_terminal_counts={"gs-test": gs_terminals},
            **_policy_kwargs(policy, "mbb" if mbb_reserve > 0 else "bbm"),
            gs_min_elevations={"gs-test": min_elev},
            gs_service_priorities={"gs-test": 10},
            gs_tenant_ids={"gs-test": "default"},
            gs_reference_bodies={"gs-test": "earth"},
            sat_ground_terminals=sat_terminals,
            sat_ground_terminal_indices_by_body=_sat_body_pools(sat_terminals),
            mbb_overlap_ticks=3,
            mbb_reserve=mbb_reserve,
        )

    def test_active_pair_survives_when_challenger_does_not_clear_hysteresis_margin(self):
        old_pair = ("gs-test", "sat-active")

        result = self._allocate(
            [
                _visible_gv("sat-active", 40.0, 1000.0),
                _visible_gv("sat-challenger", 44.0, 900.0),
            ],
            current={old_pair: (0, 0)},
        )

        assert result.associations == {old_pair: (0, 0)}
        assert result.pending_teardowns == {}
        assert result.scheduled_pairs == frozenset({old_pair})

    def test_challenger_starts_make_before_break_when_it_clears_hysteresis_margin(self):
        old_pair = ("gs-test", "sat-active")
        new_pair = ("gs-test", "sat-challenger")

        result = self._allocate(
            [
                _visible_gv("sat-active", 40.0, 1000.0),
                _visible_gv("sat-challenger", 47.0, 900.0),
            ],
            current={old_pair: (0, 0)},
        )

        assert result.associations == {
            old_pair: (0, 0),
            new_pair: (1, 0),
        }
        assert result.pending_teardowns == {old_pair: MbbTeardown(10, new_pair)}
        assert result.scheduled_pairs == frozenset({old_pair, new_pair})

    def test_without_current_association_highest_elevation_wins_normally(self):
        result = self._allocate(
            [
                _visible_gv("sat-lower", 40.0, 1000.0),
                _visible_gv("sat-higher", 44.0, 900.0),
            ],
            current={},
            gs_terminals=1,
            mbb_reserve=0,
        )

        assert result.associations == {("gs-test", "sat-higher"): (0, 0)}
        assert result.pending_teardowns == {}

    def test_lowest_elevation_policy_applies_hysteresis_to_policy_score(self):
        old_pair = ("gs-test", "sat-active")

        result = self._allocate(
            [
                _visible_gv("sat-active", 50.0, 1000.0),
                _visible_gv("sat-challenger", 45.0, 900.0),
            ],
            current={old_pair: (0, 0)},
            policy="lowest-elevation",
        )

        assert result.associations == {old_pair: (0, 0)}
        assert result.pending_teardowns == {}

    def test_multi_terminal_replacement_uses_discounted_worst_active_pair(self):
        old_pairs = {
            ("gs-test", "sat-active-40"): (0, 0),
            ("gs-test", "sat-active-41"): (1, 0),
            ("gs-test", "sat-active-42"): (2, 0),
        }

        result = self._allocate(
            [
                _visible_gv("sat-active-40", 40.0, 1000.0),
                _visible_gv("sat-active-41", 41.0, 1000.0),
                _visible_gv("sat-active-42", 42.0, 1000.0),
                _visible_gv("sat-challenger", 45.5, 900.0),
            ],
            current=old_pairs,
            gs_terminals=4,
        )

        assert result.associations == old_pairs
        assert result.pending_teardowns == {}
        assert result.scheduled_pairs == frozenset(old_pairs)

    def test_mask_fade_partial_discount_protects_near_boundary_active_pair(self):
        old_pair = ("gs-test", "sat-active")

        result = self._allocate(
            [
                _visible_gv("sat-active", 27.0, 1000.0),
                _visible_gv("sat-challenger", 28.0, 900.0),
            ],
            current={old_pair: (0, 0)},
            min_elev=25.0,
        )

        assert result.associations == {old_pair: (0, 0)}
        assert result.pending_teardowns == {}
