# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for service_priority strict preemption in ground link allocation.

Lower service_priority value = higher priority. Priority-1 segments
get first pick of satellite capacity before priority-10 segments,
regardless of score. This is strict preemption, not weighted scoring.
"""

from __future__ import annotations

from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import _compute_effective_discount, _compute_pair_score


def _score_and_sort(pairs_config):
    """Helper: score pairs and sort with the production tuple key.

    pairs_config: list of (gs_id, sat_id, elevation, priority, is_active, min_elev)
    Returns sorted list of (priority, score, gs_id, sat_id).
    """
    hyst = HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)
    policy = "highest-elevation"

    scored = []
    for gs_id, sat_id, elev, priority, is_active, min_elev in pairs_config:
        score = _compute_pair_score(elev, policy)
        if is_active:
            discount = _compute_effective_discount(elev, min_elev, hyst)
            score *= discount
        scored.append((priority, score, gs_id, sat_id))

    scored.sort(key=lambda x: (x[0], -x[1]))
    return scored


class TestServicePriorityPreemption:
    def test_priority_strict_preemption(self):
        """Priority-1 GS at 30° beats priority-10 GS at 85°."""
        sorted_pairs = _score_and_sort(
            [
                ("gs-gold", "sat-A", 30.0, 1, False, 25.0),
                ("gs-silver", "sat-A", 85.0, 10, False, 25.0),
            ]
        )
        assert sorted_pairs[0][2] == "gs-gold"

    def test_priority_hysteresis_interaction(self):
        """Priority-10 GS with active discount still loses to priority-1 GS."""
        sorted_pairs = _score_and_sort(
            [
                ("gs-gold", "sat-A", 30.0, 1, False, 25.0),
                ("gs-silver", "sat-A", 40.0, 10, True, 25.0),  # active, boosted to 46
            ]
        )
        # gold at priority 1 sorts before silver at priority 10
        assert sorted_pairs[0][2] == "gs-gold"

    def test_same_priority_score_wins(self):
        """Within the same priority tier, higher score wins."""
        sorted_pairs = _score_and_sort(
            [
                ("gs-a", "sat-A", 40.0, 10, False, 25.0),
                ("gs-b", "sat-A", 60.0, 10, False, 25.0),
            ]
        )
        assert sorted_pairs[0][2] == "gs-b"  # 60 > 40

    def test_same_priority_hysteresis_wins(self):
        """Within the same tier, hysteresis discount resolves the tiebreak."""
        sorted_pairs = _score_and_sort(
            [
                ("gs-active", "sat-A", 40.0, 10, True, 25.0),  # boosted to 46
                ("gs-challenger", "sat-A", 44.0, 10, False, 25.0),  # raw 44
            ]
        )
        assert sorted_pairs[0][2] == "gs-active"  # 46 > 44

    def test_three_tiers(self):
        """Three priority levels sort correctly."""
        sorted_pairs = _score_and_sort(
            [
                ("gs-low", "sat-A", 80.0, 20, False, 25.0),
                ("gs-high", "sat-A", 30.0, 1, False, 25.0),
                ("gs-mid", "sat-A", 60.0, 5, False, 25.0),
            ]
        )
        assert sorted_pairs[0][2] == "gs-high"  # priority 1
        assert sorted_pairs[1][2] == "gs-mid"  # priority 5
        assert sorted_pairs[2][2] == "gs-low"  # priority 20
