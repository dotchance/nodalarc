# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Direct tests for the OME ground allocation engine."""

from __future__ import annotations

from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import allocate_ground_links
from ome.types import MbbTeardown
from ome.visibility import GroundVisibility


def _allocate(
    visible: list[GroundVisibility],
    *,
    current: dict[tuple[str, str], tuple[int, int]] | None = None,
    pending: dict[tuple[str, str], MbbTeardown] | None = None,
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
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals=sat_caps,
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
    )


def test_highest_elevation_selects_best_visible_candidate():
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-low",
                visible=True,
                elevation_deg=35.0,
                range_km=1200.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-high",
                visible=True,
                elevation_deg=55.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ]
    )

    assert result.associations == {("gs-A", "sat-high"): (0, 0)}
    assert result.scheduled_pairs == frozenset({("gs-A", "sat-high")})
    assert result.pending_teardowns == {}


def test_longest_remaining_pass_selects_longest_sampled_dwell_not_highest_elevation():
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-high-short",
                visible=True,
                elevation_deg=70.0,
                range_km=900.0,
                remaining_visible_s=5.0,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-low-long",
                visible=True,
                elevation_deg=30.0,
                range_km=1200.0,
                remaining_visible_s=60.0,
                reject_reason="ok",
            ),
        ],
        policy="longest-remaining-pass",
    )

    assert result.associations == {("gs-A", "sat-low-long"): (0, 0)}
    assert result.scheduled_pairs == frozenset({("gs-A", "sat-low-long")})


def test_mbb_replacement_starts_teardown_when_challenger_beats_hysteresis():
    old_pair = ("gs-A", "sat-old")
    new_pair = ("gs-A", "sat-new")

    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-old",
                visible=True,
                elevation_deg=40.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-new",
                visible=True,
                elevation_deg=47.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={old_pair: (0, 0)},
        gs_terminals=2,
        mbb_reserve=1,
    )

    assert result.associations == {
        old_pair: (0, 0),
        new_pair: (1, 0),
    }
    assert result.pending_teardowns == {old_pair: MbbTeardown(10, new_pair)}
    assert result.scheduled_pairs == frozenset({old_pair, new_pair})


def test_hysteresis_prevents_mbb_replacement_when_challenger_does_not_clear_margin():
    old_pair = ("gs-A", "sat-old")

    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-old",
                visible=True,
                elevation_deg=40.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-new",
                visible=True,
                elevation_deg=44.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
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
                GroundVisibility(
                    sat_id="sat-old",
                    visible=True,
                    elevation_deg=40.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
                GroundVisibility(
                    sat_id="sat-new",
                    visible=True,
                    elevation_deg=47.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0), new_pair: (1, 0)},
        pending_teardowns={old_pair: MbbTeardown(10, new_pair)},
        gs_terminal_counts={"gs-A": 2},
        gs_policies={"gs-A": "highest-elevation"},
        gs_min_elevations={"gs-A": 25.0},
        gs_hysteresis={"gs-A": HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0)},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-new": 1},
        mbb_overlap_ticks=3,
        mbb_reserve=1,
    )

    assert result.associations == {new_pair: (1, 0)}
    assert result.pending_teardowns == {}
    assert result.scheduled_pairs == frozenset({new_pair})


# ---------------------------------------------------------------------------
# UnscheduledPair emission (Phase 1.3.a)
#
# Every visible pair that the allocator rejects MUST appear in
# result.unscheduled_pairs with a typed reason. Tests below pin each
# rejection branch.
# ---------------------------------------------------------------------------


def test_unscheduled_pairs_empty_when_all_visible_pairs_allocated():
    """The happy path: GS has spare terminal capacity, sat has spare
    terminal capacity, the candidate is allocated. No rejection record."""
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-a",
                visible=True,
                elevation_deg=55.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            )
        ],
        gs_terminals=2,
    )
    assert result.unscheduled_pairs == ()


def test_unscheduled_pair_sat_capacity_exhausted():
    """Two GSes compete for the same sat which has ground_terminal_count=1.
    The losing GS gets unscheduled_reason='sat_capacity' with the
    incumbent pair named."""
    visible = [
        GroundVisibility(
            sat_id="sat-shared",
            visible=True,
            elevation_deg=55.0,
            range_km=900.0,
            remaining_visible_s=None,
            reject_reason="ok",
        )
    ]
    result = allocate_ground_links(
        step=0,
        visible_per_station={"gs-A": visible, "gs-B": visible},
        ground_station_ids={"gs-A", "gs-B"},
        current_associations={},
        pending_teardowns={},
        gs_terminal_counts={"gs-A": 2, "gs-B": 2},
        gs_policies={"gs-A": "highest-elevation", "gs-B": "highest-elevation"},
        gs_min_elevations={"gs-A": 25.0, "gs-B": 25.0},
        gs_hysteresis={
            "gs-A": HysteresisParameters(),
            "gs-B": HysteresisParameters(),
        },
        gs_service_priorities={"gs-A": 10, "gs-B": 10},
        gs_tenant_ids={"gs-A": "default", "gs-B": "default"},
        gs_reference_bodies={"gs-A": "earth", "gs-B": "earth"},
        sat_ground_terminals={"sat-shared": 1},
        mbb_overlap_ticks=3,
        mbb_reserve=0,
    )

    # Exactly one of the two pairs wins; the other carries the reason.
    assert len(result.associations) == 1
    assert len(result.unscheduled_pairs) == 1
    rejected = result.unscheduled_pairs[0]
    assert rejected.unscheduled_reason == "sat_capacity"
    assert rejected.tenant_id == "default"
    assert rejected.reference_body == "earth"
    assert rejected.incumbent_pair is not None  # the winning pair
    assert rejected.capacity_constraint == "sat-shared.ground_terminals"


def test_unscheduled_pair_bbm_no_spare_on_single_terminal_gs():
    """1-terminal GS with an incumbent: a higher-elevation challenger
    cannot displace it (no BBM-displacement path). The challenger
    appears with reason='bbm_no_spare'. This is Finding 2 in the
    foundations plan — the test pins the behavior so the future fix
    (selection/handover split) MUST update this test deliberately."""
    incumbent = ("gs-A", "sat-incumbent")
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-incumbent",
                visible=True,
                elevation_deg=26.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-challenger",
                visible=True,
                elevation_deg=80.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={incumbent: (0, 0)},
        gs_terminals=1,
    )

    assert result.associations == {incumbent: (0, 0)}
    challengers = [u for u in result.unscheduled_pairs if "sat-challenger" in u.pair]
    assert len(challengers) == 1
    rejected = challengers[0]
    assert rejected.unscheduled_reason == "bbm_no_spare"
    assert rejected.incumbent_pair == incumbent


def test_unscheduled_pair_hysteresis_hold_on_multi_terminal_displacement():
    """Multi-terminal GS at logical capacity (tc=2, mbb_reserve=1,
    1 active steady link). A challenger competes for displacement but
    its score does not beat the incumbent's hysteresis-discounted
    score. The challenger appears with reason='hysteresis_hold'."""
    incumbent = ("gs-A", "sat-incumbent")
    # Incumbent at 40°, challenger at 44° — within typical hysteresis
    # margin so displacement should not fire with default discount_factor=1.15.
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-incumbent",
                visible=True,
                elevation_deg=40.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-challenger",
                visible=True,
                elevation_deg=44.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={incumbent: (0, 0)},
        gs_terminals=2,
        mbb_reserve=1,
    )

    assert result.associations == {incumbent: (0, 0)}
    challengers = [u for u in result.unscheduled_pairs if "sat-challenger" in u.pair]
    assert len(challengers) == 1
    assert challengers[0].unscheduled_reason == "hysteresis_hold"


def test_unscheduled_pair_replaced_by_successor_when_teardown_expires():
    """Pair was in pending_teardowns from a previous tick. On this
    tick the overlap window has expired AND the pair is still visible.
    The OME has released it in favor of the successor; the
    unscheduled_reason='replaced_by_successor' with incumbent_pair
    pointing to the successor that replaced it."""
    old_pair = ("gs-A", "sat-old")
    new_pair = ("gs-A", "sat-new")

    result = allocate_ground_links(
        step=13,  # 10 + 3 overlap = expired
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-old",
                    visible=True,
                    elevation_deg=40.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
                GroundVisibility(
                    sat_id="sat-new",
                    visible=True,
                    elevation_deg=47.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0), new_pair: (1, 0)},
        pending_teardowns={old_pair: MbbTeardown(10, new_pair)},
        gs_terminal_counts={"gs-A": 2},
        gs_policies={"gs-A": "highest-elevation"},
        gs_min_elevations={"gs-A": 25.0},
        gs_hysteresis={"gs-A": HysteresisParameters()},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-new": 1},
        mbb_overlap_ticks=3,
        mbb_reserve=1,
    )

    # Successor is now the sole steady-state association.
    assert result.associations == {new_pair: (1, 0)}
    # Old pair appears as replaced_by_successor.
    olds = [u for u in result.unscheduled_pairs if u.pair == old_pair]
    assert len(olds) == 1
    assert olds[0].unscheduled_reason == "replaced_by_successor"
    assert olds[0].incumbent_pair == new_pair


def test_unscheduled_pairs_are_deterministically_sorted():
    """Direction 4 (multi-compute-node): two Scheduler replicas
    receiving the same GroundLinkDecisionSnapshot must see the same
    ordering of unscheduled_pairs."""
    # Build a scenario where multiple pairs reject for different reasons.
    incumbent = ("gs-A", "sat-incumbent")
    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-incumbent",
                visible=True,
                elevation_deg=40.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-z-late",
                visible=True,
                elevation_deg=41.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-a-early",
                visible=True,
                elevation_deg=42.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={incumbent: (0, 0)},
        gs_terminals=1,
    )

    assert result.associations == {incumbent: (0, 0)}
    pairs = [u.pair for u in result.unscheduled_pairs]
    assert pairs == sorted(pairs)


def test_missing_tenant_id_fails_loudly():
    """Direction 2: every unscheduled-pair record carries tenant scope.
    Missing per-GS tenant_id is fatal at the allocator boundary."""
    import pytest

    with pytest.raises(ValueError, match="tenant_id"):
        allocate_ground_links(
            step=0,
            visible_per_station={
                "gs-A": [
                    GroundVisibility(
                        sat_id="sat-a",
                        visible=True,
                        elevation_deg=40.0,
                        range_km=1000.0,
                        remaining_visible_s=None,
                        reject_reason="ok",
                    )
                ]
            },
            ground_station_ids={"gs-A"},
            current_associations={},
            pending_teardowns={},
            gs_terminal_counts={"gs-A": 1},
            gs_policies={"gs-A": "highest-elevation"},
            gs_min_elevations={"gs-A": 25.0},
            gs_hysteresis={"gs-A": HysteresisParameters()},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={},  # empty — must fail
            gs_reference_bodies={"gs-A": "earth"},
            sat_ground_terminals={"sat-a": 1},
            mbb_overlap_ticks=3,
            mbb_reserve=0,
        )


def test_missing_reference_body_fails_loudly():
    """Direction 3: every unscheduled-pair record carries body anchor.
    Missing per-GS reference_body is fatal at the allocator boundary."""
    import pytest

    with pytest.raises(ValueError, match="reference_body"):
        allocate_ground_links(
            step=0,
            visible_per_station={
                "gs-A": [
                    GroundVisibility(
                        sat_id="sat-a",
                        visible=True,
                        elevation_deg=40.0,
                        range_km=1000.0,
                        remaining_visible_s=None,
                        reject_reason="ok",
                    )
                ]
            },
            ground_station_ids={"gs-A"},
            current_associations={},
            pending_teardowns={},
            gs_terminal_counts={"gs-A": 1},
            gs_policies={"gs-A": "highest-elevation"},
            gs_min_elevations={"gs-A": 25.0},
            gs_hysteresis={"gs-A": HysteresisParameters()},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={"gs-A": "default"},
            gs_reference_bodies={},  # empty — must fail
            sat_ground_terminals={"sat-a": 1},
            mbb_overlap_ticks=3,
            mbb_reserve=0,
        )
