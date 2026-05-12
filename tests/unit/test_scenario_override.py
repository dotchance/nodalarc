"""Test scenario override state and _build_dispatch_intent composition.

Tests that override state (_override_pairs, _override_nodes) correctly
filters _desired_links via _build_dispatch_intent, that node-level
overrides suppress all pairs involving the node, and that pair
normalization works at the intent builder level.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from scheduler.dispatcher import ActiveLinkInfo, Dispatcher


def _make_dispatcher(**overrides) -> Dispatcher:
    defaults = dict(
        interface_map={},
        bandwidth_map={},
        pod_locator=MagicMock(),
        agent_pool=MagicMock(),
        session_id="test-session",
        wiring_generation="sha256:" + "a" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={},
        sat_ground_terminal_capacities={},
    )
    defaults.update(overrides)
    return Dispatcher(**defaults)


SIM_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class TestOverridePairs:
    def test_add_pair_suppresses_from_effective_desired(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert pair not in intent.desired

    def test_remove_pair_restores_to_effective_desired(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_pairs[pair] = "scenario_inject_down"
        d._override_pairs.pop(pair)
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert pair in intent.desired

    def test_clear_overrides_restores_all(self):
        d = _make_dispatcher()
        pairs = [("sat-P00S00", "sat-P00S01"), ("sat-P00S02", "sat-P00S03")]
        for p in pairs:
            d._desired_links[p] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")
            d._override_pairs[p] = "scenario_inject_down"

        d._override_pairs.clear()
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        for p in pairs:
            assert p in intent.desired

    def test_desired_links_not_modified_by_override(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_pairs[pair] = "scenario_inject_down"
        d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert pair in d._desired_links


class TestOverrideNodes:
    def test_node_override_suppresses_all_pairs_involving_node(self):
        d = _make_dispatcher()
        d._desired_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0", "isl1", 3.0, 1000.0, link_type="isl"
        )
        d._desired_links[("sat-P00S00", "sat-P01S00")] = ActiveLinkInfo(
            "isl1", "isl0", 3.0, 1000.0, link_type="isl"
        )
        d._desired_links[("sat-P00S01", "sat-P01S00")] = ActiveLinkInfo(
            "isl2", "isl2", 3.0, 1000.0, link_type="isl"
        )

        d._override_nodes["sat-P00S00"] = "satellite_loss"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert ("sat-P00S00", "sat-P00S01") not in intent.desired
        assert ("sat-P00S00", "sat-P01S00") not in intent.desired
        assert ("sat-P00S01", "sat-P01S00") in intent.desired

    def test_node_override_suppresses_even_without_pair_override(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_nodes["sat-P00S00"] = "satellite_loss"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert pair not in intent.desired
        assert pair not in d._override_pairs

    def test_restore_node_unsuppresses(self):
        d = _make_dispatcher()
        d._desired_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0", "isl1", 3.0, 1000.0, link_type="isl"
        )
        d._override_nodes["sat-P00S00"] = "satellite_loss"
        d._override_nodes.pop("sat-P00S00")

        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")
        assert ("sat-P00S00", "sat-P00S01") in intent.desired


class TestReasonCapture:
    def test_pair_override_captured_in_down_reasons(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert intent.down_reasons[pair] == "scenario_inject_down"
        assert pair in intent.forced_bbm_pairs

    def test_node_override_captured_in_down_reasons(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_nodes["sat-P00S00"] = "satellite_loss"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert intent.down_reasons[pair] == "satellite_loss"
        assert pair in intent.forced_bbm_pairs

    def test_no_override_reason_for_ome_removed_pairs(self):
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="ome_event")

        assert pair not in intent.down_reasons
        assert pair not in intent.forced_bbm_pairs

    def test_reason_from_desired_union_actual(self):
        """Override reason captured for pairs in desired but not yet in actual."""
        d = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0, link_type="isl")

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert pair in intent.down_reasons


class TestPairNormalization:
    def test_canonical_ordering(self):
        d = _make_dispatcher()
        d._desired_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0", "isl1", 3.0, 1000.0, link_type="isl"
        )

        d._override_pairs[("sat-P00S00", "sat-P00S01")] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM_TIME, source="scenario")

        assert ("sat-P00S00", "sat-P00S01") not in intent.desired
        assert ("sat-P00S00", "sat-P00S01") in intent.down_reasons
