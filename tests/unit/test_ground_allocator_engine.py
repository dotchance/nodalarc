# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Direct tests for the OME ground allocation engine."""

from __future__ import annotations

from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import allocate_ground_links
from ome.visibility import GroundVisibility


def _allocate(
    visible: list[GroundVisibility],
    *,
    current: dict[tuple[str, str], tuple[int, int]] | None = None,
    pending: dict[tuple[str, str], tuple[int, tuple[str, str]]] | None = None,
    policy: str = "highest-elevation",
    gs_terminals: int = 1,
    sat_terminals: dict[str, int] | None = None,
    mbb_overlap_ticks: int = 3,
    mbb_reserve: int = 0,
):
    sat_caps = sat_terminals or {gv.sat_id: 1 for gv in visible}
    return allocate_ground_links(
        step=10,
        visible_per_station={"gs-A": visible},
        ground_station_ids={"gs-A"},
        current_associations=current or {},
        pending_teardowns=pending or {},
        gs_terminal_counts={"gs-A": gs_terminals},
        gs_policies={"gs-A": policy},
        gs_min_elevations={"gs-A": 25.0},
        gs_hysteresis={"gs-A": HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)},
        gs_service_priorities={"gs-A": 10},
        sat_ground_terminals=sat_caps,
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
    )


def test_highest_elevation_selects_best_visible_candidate():
    result = _allocate(
        [
            GroundVisibility("sat-low", True, 35.0, 1200.0),
            GroundVisibility("sat-high", True, 55.0, 900.0),
        ]
    )

    assert result.associations == {("gs-A", "sat-high"): (0, 0)}
    assert result.scheduled_pairs == frozenset({("gs-A", "sat-high")})
    assert result.pending_teardowns == {}


def test_mbb_replacement_starts_teardown_when_challenger_beats_hysteresis():
    old_pair = ("gs-A", "sat-old")
    new_pair = ("gs-A", "sat-new")

    result = _allocate(
        [
            GroundVisibility("sat-old", True, 40.0, 1000.0),
            GroundVisibility("sat-new", True, 47.0, 900.0),
        ],
        current={old_pair: (0, 0)},
        gs_terminals=2,
        mbb_reserve=1,
    )

    assert result.associations == {
        old_pair: (0, 0),
        new_pair: (1, 0),
    }
    assert result.pending_teardowns == {old_pair: (10, new_pair)}
    assert result.scheduled_pairs == frozenset({old_pair, new_pair})


def test_hysteresis_prevents_mbb_replacement_when_challenger_does_not_clear_margin():
    old_pair = ("gs-A", "sat-old")

    result = _allocate(
        [
            GroundVisibility("sat-old", True, 40.0, 1000.0),
            GroundVisibility("sat-new", True, 44.0, 900.0),
        ],
        current={old_pair: (0, 0)},
        gs_terminals=2,
        mbb_reserve=1,
    )

    assert result.associations == {old_pair: (0, 0)}
    assert result.pending_teardowns == {}
    assert result.scheduled_pairs == frozenset({old_pair})


def test_pending_teardown_expires_after_overlap_window():
    old_pair = ("gs-A", "sat-old")
    new_pair = ("gs-A", "sat-new")

    result = allocate_ground_links(
        step=13,
        visible_per_station={
            "gs-A": [
                GroundVisibility("sat-old", True, 40.0, 1000.0),
                GroundVisibility("sat-new", True, 47.0, 900.0),
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0), new_pair: (1, 0)},
        pending_teardowns={old_pair: (10, new_pair)},
        gs_terminal_counts={"gs-A": 2},
        gs_policies={"gs-A": "highest-elevation"},
        gs_min_elevations={"gs-A": 25.0},
        gs_hysteresis={"gs-A": HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)},
        gs_service_priorities={"gs-A": 10},
        sat_ground_terminals={"sat-old": 1, "sat-new": 1},
        mbb_overlap_ticks=3,
        mbb_reserve=1,
    )

    assert result.associations == {new_pair: (1, 0)}
    assert result.pending_teardowns == {}
    assert result.scheduled_pairs == frozenset({new_pair})
