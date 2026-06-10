# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Direct tests for the OME ground allocation engine."""

from __future__ import annotations

from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import allocate_ground_links
from ome.types import MbbTeardown
from ome.visibility import GroundVisibility


def _selection_policy(name: str) -> SelectionPolicySpec:
    params = {"lookahead_horizon_ticks": 600} if name == "longest-remaining-pass" else {}
    return SelectionPolicySpec(name=name, params=params)


def _handover_policy(name: str = "hysteresis") -> HandoverPolicySpec:
    params = (
        HysteresisParameters(
            discount_factor=1.15,
            mask_fade_range_deg=5.0,
        ).model_dump()
        if name == "hysteresis"
        else {}
    )
    return HandoverPolicySpec(name=name, params=params)


def _policy_kwargs(
    gs_ids: set[str],
    *,
    policy: str = "highest-elevation",
    handover_policy: str = "hysteresis",
    handover_mode: str = "bbm",
    mbb_overlap_ticks: int | None = None,
    mbb_reserve: int | None = None,
) -> dict:
    overlap = 3 if handover_mode == "mbb" else 0
    reserve = 1 if handover_mode == "mbb" else 0
    if mbb_overlap_ticks is not None:
        overlap = mbb_overlap_ticks
    if mbb_reserve is not None:
        reserve = mbb_reserve
    return {
        "gs_selection_policies": {gs_id: _selection_policy(policy) for gs_id in gs_ids},
        "gs_handover_policies": {gs_id: _handover_policy(handover_policy) for gs_id in gs_ids},
        "gs_handover_modes": dict.fromkeys(gs_ids, handover_mode),
        "gs_mbb_overlap_ticks": dict.fromkeys(gs_ids, overlap),
        "gs_mbb_reserve": dict.fromkeys(gs_ids, reserve),
        "ranking_order": (
            "service_priority",
            "selection_score",
            "satellite_ground_terminal_capacity",
            "lex_pair",
        ),
        "mbb_preemption": "off",
        "successor_abort_policy": "hard_release",
        "cross_tenant_displacement": "off",
        "bbm_acquire_timeout_ticks": 1,
        "ignored_capacity_fields": (),
    }


def _sat_body_pools(
    sat_terminals: dict[str, int],
    *,
    reference_body: str = "earth",
) -> dict[str, dict[str, tuple[int, ...]]]:
    return {
        sat_id: {reference_body: tuple(range(count))} for sat_id, count in sat_terminals.items()
    }


def _allocate(
    visible: list[GroundVisibility],
    *,
    current: dict[tuple[str, str], tuple[int, int]] | None = None,
    pending: dict[tuple[str, str], MbbTeardown] | None = None,
    policy: str = "highest-elevation",
    handover_policy: str = "hysteresis",
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
        **_policy_kwargs(
            {"gs-A"},
            policy=policy,
            handover_policy=handover_policy,
            handover_mode="mbb" if mbb_reserve > 0 else "bbm",
        ),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals=sat_caps,
        sat_ground_terminal_indices_by_body=_sat_body_pools(sat_caps),
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
    start_events = [
        event for event in result.allocation_events if event.category == "mbb_overlap_started"
    ]
    assert len(start_events) == 1
    assert start_events[0].pair == old_pair
    assert start_events[0].successor_pair == new_pair


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
        **_policy_kwargs({"gs-A"}, handover_mode="mbb"),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-new": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-old": 1, "sat-new": 1}),
    )

    assert result.associations == {new_pair: (1, 0)}
    assert result.pending_teardowns == {}
    assert result.scheduled_pairs == frozenset({new_pair})
    completed = [
        event for event in result.lifecycle_events if event.category == "teardown_completed"
    ]
    assert len(completed) == 1
    assert completed[0].old_pair == old_pair
    assert completed[0].successor_pair == new_pair


# ---------------------------------------------------------------------------
# UnscheduledPair emission
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
        **_policy_kwargs({"gs-A", "gs-B"}),
        gs_min_elevations={"gs-A": 25.0, "gs-B": 25.0},
        gs_service_priorities={"gs-A": 10, "gs-B": 10},
        gs_tenant_ids={"gs-A": "default", "gs-B": "default"},
        gs_reference_bodies={"gs-A": "earth", "gs-B": "earth"},
        sat_ground_terminals={"sat-shared": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-shared": 1}),
    )

    # Exactly one of the two pairs wins; the other carries the reason.
    assert len(result.associations) == 1
    assert len(result.unscheduled_pairs) == 1
    rejected = result.unscheduled_pairs[0]
    assert rejected.unscheduled_reason == "sat_capacity"
    assert rejected.tenant_id == "default"
    assert rejected.reference_body == "earth"
    assert rejected.incumbent_pair is not None  # the winning pair
    assert rejected.capacity_constraint == "sat-shared.ground_terminals[earth]"


def test_bbm_single_terminal_gs_displaces_when_challenger_clears_handover_policy():
    """BBM does not need a spare terminal. A single-terminal GS can release
    the incumbent and acquire the chosen challenger in the same allocator tick."""
    incumbent = ("gs-A", "sat-incumbent")
    challenger = ("gs-A", "sat-challenger")
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

    assert result.associations == {challenger: (0, 0)}
    assert result.pending_teardowns == {}
    assert result.scheduled_pairs == frozenset({challenger})
    olds = [u for u in result.unscheduled_pairs if u.pair == incumbent]
    assert len(olds) == 1
    assert olds[0].unscheduled_reason == "replaced_by_successor"
    assert olds[0].incumbent_pair == challenger
    bbm_events = [event for event in result.allocation_events if event.category == "bbm_gap"]
    assert len(bbm_events) == 1
    assert bbm_events[0].policy_kind == "handover_mode"
    assert bbm_events[0].policy_name == "bbm"


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
        **_policy_kwargs({"gs-A"}, handover_mode="mbb"),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-new": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-old": 1, "sat-new": 1}),
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


def test_current_incumbent_visibility_loss_emits_allocation_event():
    incumbent = ("gs-A", "sat-incumbent")

    result = _allocate(
        [],
        current={incumbent: (0, 0)},
        sat_terminals={"sat-incumbent": 1},
    )

    assert result.associations == {}
    assert result.unscheduled_pairs == ()
    assert len(result.allocation_events) == 1
    event = result.allocation_events[0]
    assert event.category == "incumbent_lost"
    assert event.pair == incumbent
    assert event.policy_kind is None
    assert event.policy_name is None


def test_allocator_rejects_unsupported_multi_overlap_mbb_reserve():
    import pytest

    with pytest.raises(ValueError, match="multi-overlap allocator support"):
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
            gs_terminal_counts={"gs-A": 4},
            **_policy_kwargs({"gs-A"}, handover_mode="mbb", mbb_reserve=2),
            gs_min_elevations={"gs-A": 25.0},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={"gs-A": "default"},
            gs_reference_bodies={"gs-A": "earth"},
            sat_ground_terminals={"sat-a": 4},
            sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-a": 4}),
        )


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
            **_policy_kwargs({"gs-A"}),
            gs_min_elevations={"gs-A": 25.0},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={},  # empty — must fail
            gs_reference_bodies={"gs-A": "earth"},
            sat_ground_terminals={"sat-a": 1},
            sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-a": 1}),
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
            **_policy_kwargs({"gs-A"}),
            gs_min_elevations={"gs-A": 25.0},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={"gs-A": "default"},
            gs_reference_bodies={},  # empty — must fail
            sat_ground_terminals={"sat-a": 1},
            sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-a": 1}),
        )


def test_handover_policy_none_displaces_without_hysteresis_margin():
    """Selection picks the raw winner; handover_policy=none allows immediate BBM displacement."""
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
                elevation_deg=41.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={old_pair: (0, 0)},
        handover_policy="none",
        gs_terminals=1,
    )

    assert result.associations == {new_pair: (0, 0)}
    assert result.pending_teardowns == {}
    replaced = [u for u in result.unscheduled_pairs if u.pair == old_pair]
    assert len(replaced) == 1
    assert replaced[0].unscheduled_reason == "replaced_by_successor"


def test_default_ranking_order_prefers_candidate_specific_scarce_satellite_capacity():
    """Default ties prefer the satellite with fewer terminals for the candidate body."""
    result = allocate_ground_links(
        step=0,
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-a-wide",
                    visible=True,
                    elevation_deg=50.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
                GroundVisibility(
                    sat_id="sat-z-scarce",
                    visible=True,
                    elevation_deg=50.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={},
        pending_teardowns={},
        gs_terminal_counts={"gs-A": 1},
        **_policy_kwargs({"gs-A"}),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-a-wide": 3, "sat-z-scarce": 3},
        sat_ground_terminal_indices_by_body={
            "sat-a-wide": {"earth": (0, 1), "luna": (2,)},
            "sat-z-scarce": {"earth": (0,), "luna": (1, 2)},
        },
    )

    assert result.associations == {("gs-A", "sat-z-scarce"): (0, 0)}
    assert result.policy_audit.ranking_order == (
        "service_priority",
        "selection_score",
        "satellite_ground_terminal_capacity",
        "lex_pair",
    )


def test_configured_ranking_order_can_prioritize_per_gs_rank_before_service_priority():
    """Determinism is mechanism; ranking component order is operator policy."""
    visible_a = [
        GroundVisibility(
            sat_id="sat-shared",
            visible=True,
            elevation_deg=80.0,
            range_km=900.0,
            remaining_visible_s=None,
            reject_reason="ok",
        )
    ]
    visible_b = [
        GroundVisibility(
            sat_id="sat-shared",
            visible=True,
            elevation_deg=30.0,
            range_km=900.0,
            remaining_visible_s=None,
            reject_reason="ok",
        )
    ]

    result = allocate_ground_links(
        step=0,
        visible_per_station={"gs-A": visible_a, "gs-B": visible_b},
        ground_station_ids={"gs-A", "gs-B"},
        current_associations={},
        pending_teardowns={},
        gs_terminal_counts={"gs-A": 1, "gs-B": 1},
        **{
            **_policy_kwargs({"gs-A", "gs-B"}),
            "ranking_order": ("selection_score", "service_priority", "lex_pair"),
        },
        gs_min_elevations={"gs-A": 25.0, "gs-B": 25.0},
        gs_service_priorities={"gs-A": 20, "gs-B": 1},
        gs_tenant_ids={"gs-A": "default", "gs-B": "default"},
        gs_reference_bodies={"gs-A": "earth", "gs-B": "earth"},
        sat_ground_terminals={"sat-shared": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-shared": 1}),
    )

    assert result.associations == {("gs-A", "sat-shared"): (0, 0)}
    assert result.unscheduled_pairs[0].pair == ("gs-B", "sat-shared")
    assert result.policy_audit.ranking_order == ("selection_score", "service_priority", "lex_pair")


def test_mbb_failed_successor_hard_release_drops_visible_old_pair():
    old_pair = ("gs-A", "sat-old")
    successor_pair = ("gs-A", "sat-missing")

    result = allocate_ground_links(
        step=11,
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-old",
                    visible=True,
                    elevation_deg=45.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                )
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0)},
        pending_teardowns={old_pair: MbbTeardown(10, successor_pair)},
        gs_terminal_counts={"gs-A": 2},
        **_policy_kwargs({"gs-A"}, handover_mode="mbb"),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-missing": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-old": 1, "sat-missing": 1}),
    )

    assert result.associations == {}
    assert result.pending_teardowns == {}
    assert result.unscheduled_pairs[0].unscheduled_reason == "failed_successor"
    assert result.allocation_events[0].category == "failed_successor"
    assert len(result.lifecycle_events) == 1
    assert result.lifecycle_events[0].category == "failed_successor"
    assert result.lifecycle_events[0].old_pair == old_pair
    assert result.lifecycle_events[0].successor_pair == successor_pair


def test_mbb_visible_successor_missing_from_current_emits_failed_acquire_event():
    old_pair = ("gs-A", "sat-old")
    successor_pair = ("gs-A", "sat-successor")

    result = allocate_ground_links(
        step=11,
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-old",
                    visible=True,
                    elevation_deg=45.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
                GroundVisibility(
                    sat_id="sat-successor",
                    visible=True,
                    elevation_deg=55.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0)},
        pending_teardowns={old_pair: MbbTeardown(10, successor_pair)},
        gs_terminal_counts={"gs-A": 2},
        **_policy_kwargs({"gs-A"}, handover_mode="mbb"),
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-successor": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-old": 1, "sat-successor": 1}),
    )

    categories = [event.category for event in result.allocation_events]
    assert categories == ["failed_successor", "failed_acquire"]
    failed_acquire = result.allocation_events[1]
    assert failed_acquire.pair == successor_pair
    assert failed_acquire.policy_kind == "successor_abort_policy"
    assert failed_acquire.policy_name == "hard_release"
    rejected = {u.pair: u for u in result.unscheduled_pairs}
    assert rejected[successor_pair].unscheduled_reason == "failed_acquire"
    assert len(result.lifecycle_events) == 1
    assert result.lifecycle_events[0].category == "failed_successor"
    assert result.lifecycle_events[0].source_allocation_event_category == "failed_successor"


def test_mbb_failed_successor_soft_retain_keeps_visible_old_pair():
    old_pair = ("gs-A", "sat-old")
    successor_pair = ("gs-A", "sat-missing")
    kwargs = _policy_kwargs({"gs-A"}, handover_mode="mbb")
    kwargs["successor_abort_policy"] = "soft_retain"

    result = allocate_ground_links(
        step=11,
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-old",
                    visible=True,
                    elevation_deg=45.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                )
            ]
        },
        ground_station_ids={"gs-A"},
        current_associations={old_pair: (0, 0)},
        pending_teardowns={old_pair: MbbTeardown(10, successor_pair)},
        gs_terminal_counts={"gs-A": 2},
        **kwargs,
        gs_min_elevations={"gs-A": 25.0},
        gs_service_priorities={"gs-A": 10},
        gs_tenant_ids={"gs-A": "default"},
        gs_reference_bodies={"gs-A": "earth"},
        sat_ground_terminals={"sat-old": 1, "sat-missing": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-old": 1, "sat-missing": 1}),
    )

    assert result.associations == {old_pair: (0, 0)}
    assert result.pending_teardowns == {}
    assert result.unscheduled_pairs == ()
    assert result.allocation_events[0].category == "failed_successor"
    assert len(result.lifecycle_events) == 1
    assert result.lifecycle_events[0].category == "failed_successor"
    assert result.policy_audit.successor_abort_policy == "soft_retain"


def test_sat_capacity_rechecked_after_same_tick_release_from_other_partition():
    """Capacity attribution is not sticky within a tick.

    gs-B is evaluated first and initially loses because sat-shared is occupied
    by a different tenant's incumbent. gs-A then BBM-displaces that incumbent
    to sat-new. The allocator must re-evaluate gs-B against the updated
    capacity view and schedule it; a one-pass allocator leaves it incorrectly
    unscheduled with stale sat_capacity.
    """
    old_pair = ("gs-A", "sat-shared")
    new_pair = ("gs-A", "sat-new")
    b_pair = ("gs-B", "sat-shared")
    shared_visibility = GroundVisibility(
        sat_id="sat-shared",
        visible=True,
        elevation_deg=60.0,
        range_km=900.0,
        remaining_visible_s=None,
        reject_reason="ok",
    )

    result = allocate_ground_links(
        step=10,
        visible_per_station={
            "gs-A": [
                GroundVisibility(
                    sat_id="sat-shared",
                    visible=True,
                    elevation_deg=40.0,
                    range_km=1000.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
                GroundVisibility(
                    sat_id="sat-new",
                    visible=True,
                    elevation_deg=80.0,
                    range_km=900.0,
                    remaining_visible_s=None,
                    reject_reason="ok",
                ),
            ],
            "gs-B": [shared_visibility],
        },
        ground_station_ids={"gs-A", "gs-B"},
        current_associations={old_pair: (0, 0)},
        pending_teardowns={},
        gs_terminal_counts={"gs-A": 1, "gs-B": 1},
        **_policy_kwargs({"gs-A", "gs-B"}, handover_policy="none"),
        gs_min_elevations={"gs-A": 25.0, "gs-B": 25.0},
        gs_service_priorities={"gs-A": 10, "gs-B": 1},
        gs_tenant_ids={"gs-A": "tenant-a", "gs-B": "tenant-b"},
        gs_reference_bodies={"gs-A": "earth", "gs-B": "earth"},
        sat_ground_terminals={"sat-shared": 1, "sat-new": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-shared": 1, "sat-new": 1}),
    )

    assert result.associations == {new_pair: (0, 0), b_pair: (0, 0)}
    rejected = {u.pair: u for u in result.unscheduled_pairs}
    assert rejected[old_pair].unscheduled_reason == "replaced_by_successor"
    assert b_pair not in rejected


def test_sat_capacity_arbitration_can_displace_lower_rank_same_partition_incumbent():
    """Satellite capacity contention is resolved by configured ranking, not incumbent luck."""
    incumbent = ("gs-A", "sat-shared")
    challenger = ("gs-B", "sat-shared")
    visible = [
        GroundVisibility(
            sat_id="sat-shared",
            visible=True,
            elevation_deg=50.0,
            range_km=900.0,
            remaining_visible_s=None,
            reject_reason="ok",
        )
    ]

    result = allocate_ground_links(
        step=10,
        visible_per_station={"gs-A": visible, "gs-B": visible},
        ground_station_ids={"gs-A", "gs-B"},
        current_associations={incumbent: (0, 0)},
        pending_teardowns={},
        gs_terminal_counts={"gs-A": 1, "gs-B": 1},
        **_policy_kwargs({"gs-A", "gs-B"}),
        gs_min_elevations={"gs-A": 25.0, "gs-B": 25.0},
        gs_service_priorities={"gs-A": 10, "gs-B": 1},
        gs_tenant_ids={"gs-A": "default", "gs-B": "default"},
        gs_reference_bodies={"gs-A": "earth", "gs-B": "earth"},
        sat_ground_terminals={"sat-shared": 1},
        sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-shared": 1}),
    )

    assert result.associations == {challenger: (0, 0)}
    rejected = {u.pair: u for u in result.unscheduled_pairs}
    assert rejected[incumbent].unscheduled_reason == "sat_capacity"
    assert rejected[incumbent].incumbent_pair == challenger
    assert rejected[incumbent].capacity_constraint == "sat-shared.ground_terminals[earth]"


def test_mbb_overlap_blocks_new_challenger_when_preemption_is_off():
    old_pair = ("gs-A", "sat-old")
    successor_pair = ("gs-A", "sat-successor")
    challenger_pair = ("gs-A", "sat-challenger")

    result = _allocate(
        [
            GroundVisibility(
                sat_id="sat-old",
                visible=True,
                elevation_deg=35.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-successor",
                visible=True,
                elevation_deg=45.0,
                range_km=900.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id="sat-challenger",
                visible=True,
                elevation_deg=80.0,
                range_km=800.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
        ],
        current={old_pair: (0, 0), successor_pair: (1, 0)},
        pending={old_pair: MbbTeardown(start_step=10, successor_pair=successor_pair)},
        gs_terminals=2,
        sat_terminals={"sat-old": 1, "sat-successor": 1, "sat-challenger": 1},
        mbb_reserve=1,
    )

    assert result.associations == {old_pair: (0, 0), successor_pair: (1, 0)}
    assert result.pending_teardowns == {
        old_pair: MbbTeardown(start_step=10, successor_pair=successor_pair)
    }
    rejected = {u.pair: u for u in result.unscheduled_pairs}
    assert rejected[challenger_pair].unscheduled_reason == "mbb_overlap_locked"
    assert rejected[challenger_pair].incumbent_pair in {old_pair, successor_pair}


def test_satellite_terminal_indices_are_allocated_from_matching_reference_body_pool():
    """Terminal index assignment follows configured target-body pools, not candidate order."""

    visible = [
        GroundVisibility(
            sat_id="sat-relay",
            visible=True,
            elevation_deg=60.0,
            range_km=900.0,
            remaining_visible_s=None,
            reject_reason="ok",
        )
    ]

    result = allocate_ground_links(
        step=0,
        visible_per_station={"gs-luna": visible, "gs-earth": visible},
        ground_station_ids={"gs-earth", "gs-luna"},
        current_associations={},
        pending_teardowns={},
        gs_terminal_counts={"gs-earth": 1, "gs-luna": 1},
        **_policy_kwargs({"gs-earth", "gs-luna"}),
        gs_min_elevations={"gs-earth": 25.0, "gs-luna": 25.0},
        gs_service_priorities={"gs-earth": 10, "gs-luna": 1},
        gs_tenant_ids={"gs-earth": "default", "gs-luna": "default"},
        gs_reference_bodies={"gs-earth": "earth", "gs-luna": "luna"},
        sat_ground_terminals={"sat-relay": 2},
        sat_ground_terminal_indices_by_body={
            "sat-relay": {"earth": (0,), "luna": (1,)},
        },
    )

    assert result.associations[("gs-luna", "sat-relay")] == (0, 1)
    assert result.associations[("gs-earth", "sat-relay")] == (0, 0)
    assert result.unscheduled_pairs == ()


def test_existing_association_with_wrong_body_terminal_index_fails_loudly():
    import pytest

    pair = ("gs-luna", "sat-relay")

    with pytest.raises(ValueError, match="reference_body='luna'"):
        allocate_ground_links(
            step=0,
            visible_per_station={
                "gs-luna": [
                    GroundVisibility(
                        sat_id="sat-relay",
                        visible=True,
                        elevation_deg=60.0,
                        range_km=900.0,
                        remaining_visible_s=None,
                        reject_reason="ok",
                    )
                ]
            },
            ground_station_ids={"gs-luna"},
            current_associations={pair: (0, 0)},
            pending_teardowns={},
            gs_terminal_counts={"gs-luna": 1},
            **_policy_kwargs({"gs-luna"}),
            gs_min_elevations={"gs-luna": 25.0},
            gs_service_priorities={"gs-luna": 1},
            gs_tenant_ids={"gs-luna": "default"},
            gs_reference_bodies={"gs-luna": "luna"},
            sat_ground_terminals={"sat-relay": 2},
            sat_ground_terminal_indices_by_body={
                "sat-relay": {"earth": (0,), "luna": (1,)},
            },
        )


def test_allocator_rejects_unimplemented_multi_tick_bbm_gap_timeout():
    import pytest

    with pytest.raises(ValueError, match="bbm_acquire_timeout_ticks"):
        allocate_ground_links(
            step=0,
            visible_per_station={
                "gs-A": [
                    GroundVisibility(
                        sat_id="sat-a",
                        visible=True,
                        elevation_deg=50.0,
                        range_km=900.0,
                        remaining_visible_s=None,
                        reject_reason="ok",
                    )
                ]
            },
            ground_station_ids={"gs-A"},
            current_associations={},
            pending_teardowns={},
            gs_terminal_counts={"gs-A": 1},
            **{
                **_policy_kwargs({"gs-A"}),
                "bbm_acquire_timeout_ticks": 2,
            },
            gs_min_elevations={"gs-A": 25.0},
            gs_service_priorities={"gs-A": 10},
            gs_tenant_ids={"gs-A": "default"},
            gs_reference_bodies={"gs-A": "earth"},
            sat_ground_terminals={"sat-a": 1},
            sat_ground_terminal_indices_by_body=_sat_body_pools({"sat-a": 1}),
        )
