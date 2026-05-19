# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for pure Scheduler dispatch planning helpers."""

from __future__ import annotations

from scheduler.desired_state import ActiveLinkInfo
from scheduler.dispatch_planner import classify_mbb_changes, diff_link_state


def _info(latency: float = 3.0, range_km: float = 1000.0, link_type: str = "isl"):
    return ActiveLinkInfo(
        "isl0",
        "isl1",
        latency,
        1000.0,
        link_type=link_type,
        range_km=range_km,
    )


def test_diff_link_state_classifies_add_remove_and_latency_update():
    actual = {
        ("sat-a", "sat-b"): _info(latency=3.0, range_km=1000.0),
        ("sat-c", "sat-d"): _info(latency=4.0, range_km=1200.0),
    }
    desired = {
        ("sat-a", "sat-b"): _info(latency=3.0 + 2e-9, range_km=1000.0),
        ("sat-e", "sat-f"): _info(latency=5.0, range_km=1500.0),
    }

    diff = diff_link_state(actual, desired)

    assert diff.to_remove == {("sat-c", "sat-d")}
    assert diff.to_add == {("sat-e", "sat-f")}
    assert diff.to_update_latency == {("sat-a", "sat-b")}
    assert diff.has_changes


def test_diff_link_state_ignores_sub_tolerance_latency_jitter():
    pair = ("sat-a", "sat-b")
    diff = diff_link_state(
        {pair: _info(latency=3.0, range_km=1000.0)},
        {pair: _info(latency=3.0 + 1e-10, range_km=1000.0)},
    )

    assert not diff.has_changes


def test_mbb_classification_marks_spare_ground_segment_mbb():
    old_pair = ("gs-a", "sat-old")
    new_pair = ("gs-a", "sat-new")

    plan = classify_mbb_changes(
        to_remove={old_pair},
        to_add={new_pair},
        gs_capacities={"gs-a": 2},
        gs_active_count={"gs-a": 1},
        sat_capacities={"sat-old": 1, "sat-new": 1},
        sat_active_count={"sat-old": 1, "sat-new": 0},
    )

    assert plan.gs_downs == {"gs-a": {old_pair}}
    assert plan.gs_ups == {"gs-a": {new_pair}}
    assert plan.mbb_segments == {"gs-a"}
    assert plan.bbm_segments == set()


def test_mbb_classification_forces_bbm_without_spare_ground_capacity():
    old_pair = ("gs-a", "sat-old")
    new_pair = ("gs-a", "sat-new")

    plan = classify_mbb_changes(
        to_remove={old_pair},
        to_add={new_pair},
        gs_capacities={"gs-a": 1},
        gs_active_count={"gs-a": 1},
        sat_capacities={"sat-old": 1, "sat-new": 1},
        sat_active_count={"sat-old": 1, "sat-new": 0},
    )

    assert plan.mbb_segments == set()
    assert plan.bbm_segments == {"gs-a"}


def test_mbb_classification_escalates_forced_pair_to_segment_bbm():
    old_pair = ("gs-a", "sat-old")
    new_pair = ("gs-a", "sat-new")

    plan = classify_mbb_changes(
        to_remove={old_pair},
        to_add={new_pair},
        gs_capacities={"gs-a": 4},
        gs_active_count={"gs-a": 1},
        sat_capacities={"sat-old": 1, "sat-new": 1},
        sat_active_count={"sat-old": 1, "sat-new": 0},
        forced_bbm_pairs=frozenset({new_pair}),
    )

    assert plan.mbb_segments == set()
    assert plan.bbm_segments == {"gs-a"}


def test_mbb_classification_separates_isl_from_ground_changes():
    plan = classify_mbb_changes(
        to_remove={("sat-a", "sat-b")},
        to_add={("sat-c", "sat-d")},
        gs_capacities={"gs-a": 1},
        gs_active_count={},
        sat_capacities={},
        sat_active_count={},
    )

    assert plan.isl_downs == {("sat-a", "sat-b")}
    assert plan.isl_ups == {("sat-c", "sat-d")}
    assert plan.gs_downs == {}
    assert plan.gs_ups == {}
