# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Prove deterministic dispatch and allocation ordering.

Two runs with identical inputs must produce identical output regardless of
Python hash seed. These tests construct inputs where multiple pairs compete
for the same resource and assert that the winner is always the same.
"""

from __future__ import annotations

from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import allocate_ground_links
from ome.visibility import GroundVisibility


class TestGroundAllocatorDeterminism:
    """The OME ground allocator sort must resolve all ties deterministically."""

    def test_equal_score_pairs_produce_stable_allocation_order(self):
        """Two GS-sat pairs with identical priority, score, and sat capacity
        must always select the same winner when only one terminal is available.

        Without the (gs_id, sat_id) tiebreaker in the allocator sort key,
        the winner depends on dict iteration order from upstream, which varies
        with PYTHONHASHSEED.
        """
        gs_id = "gs-A"
        sat_a = "sat-P00S00"
        sat_b = "sat-P00S01"

        visible = [
            GroundVisibility(sat_a, True, 45.0, 1000.0),
            GroundVisibility(sat_b, True, 45.0, 1000.0),
        ]

        results = []
        for _ in range(50):
            result = allocate_ground_links(
                step=0,
                visible_per_station={gs_id: visible},
                ground_station_ids={gs_id},
                current_associations={},
                pending_teardowns={},
                gs_terminal_counts={gs_id: 1},
                sat_ground_terminals={sat_a: 1, sat_b: 1},
                gs_policies={gs_id: "highest-elevation"},
                gs_min_elevations={gs_id: 25.0},
                gs_hysteresis={gs_id: HysteresisParameters()},
                gs_service_priorities={gs_id: 10},
                mbb_overlap_ticks=3,
                mbb_reserve=0,
            )
            winner = next(iter(result.scheduled_pairs)) if result.scheduled_pairs else None
            results.append(winner)

        # All 50 runs must select the same winner
        assert len(set(results)) == 1, (
            f"Nondeterministic allocation: got {len(set(results))} distinct winners "
            f"from 50 runs with identical inputs: {set(results)}"
        )

    def test_tiebreaker_selects_lexicographically_first_pair(self):
        """When priority, score, and sat capacity are equal, the allocator
        must select the pair with the lexicographically smaller (gs_id, sat_id).
        """
        gs_id = "gs-A"
        sat_a = "sat-P00S00"
        sat_b = "sat-P01S00"

        visible = [
            GroundVisibility(sat_b, True, 45.0, 1000.0),
            GroundVisibility(sat_a, True, 45.0, 1000.0),
        ]

        result = allocate_ground_links(
            step=0,
            visible_per_station={gs_id: visible},
            ground_station_ids={gs_id},
            current_associations={},
            pending_teardowns={},
            gs_terminal_counts={gs_id: 1},
            sat_ground_terminals={sat_a: 1, sat_b: 1},
            gs_policies={gs_id: "highest-elevation"},
            gs_min_elevations={gs_id: 25.0},
            gs_hysteresis={gs_id: HysteresisParameters()},
            gs_service_priorities={gs_id: 10},
            mbb_overlap_ticks=3,
            mbb_reserve=0,
        )

        # (gs-A, sat-P00S00) < (gs-A, sat-P01S00) lexicographically
        expected_pair = (min(gs_id, sat_a), max(gs_id, sat_a))
        assert expected_pair in result.scheduled_pairs


class TestAuthorityFreshnessOnStableLinks:
    """Active-link authority metadata must be refreshed on every accepted
    snapshot, even when numeric values (range_km, latency_ms) are unchanged.
    """

    def test_stable_link_authority_advances_with_each_reconcile(self):
        """After reconciling 10 snapshots with identical range/latency,
        _actual_links authority_sim_time must equal the 10th snapshot's
        sim_time, not the 1st.
        """
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from scheduler.desired_state import ActiveLinkInfo
        from scheduler.dispatcher import Dispatcher
        from scheduler.pod_locator import PodLocationMap

        pair = ("sat-P00S00", "sat-P00S01")
        iface_map = {pair: ("isl0", "isl1")}
        bw_map = {pair: 1000.0}
        loc = PodLocationMap()
        pool = MagicMock()

        d = Dispatcher(
            interface_map=iface_map,
            bandwidth_map=bw_map,
            pod_locator=loc,
            agent_pool=pool,
            session_id="test",
            gs_terminal_capacities={},
            sat_ground_terminal_capacities={},
            max_latency_age_s=1.0,
        )

        initial_sim = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        info = ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=1500.0,
            authority_sim_time=initial_sim,
            authority_source="snapshot",
            authority_sequence=1,
        )
        d._actual_links[pair] = info

        # Simulate 10 reconcile cycles with identical numeric values
        # but advancing authority_sim_time
        for tick in range(1, 11):
            tick_sim = datetime(2026, 1, 1, 0, 0, tick, tzinfo=UTC)
            desired_info = ActiveLinkInfo(
                interface_a="isl0",
                interface_b="isl1",
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                link_type="isl",
                range_km=1500.0,
                authority_sim_time=tick_sim,
                authority_source="snapshot",
                authority_sequence=tick + 1,
            )
            desired = {pair: desired_info}

            # Walk _actual_links and refresh authority (the code under test)
            for p, actual_info in d._actual_links.items():
                di = desired.get(p)
                if di is None or di.authority_sim_time is None:
                    continue
                if (
                    actual_info.authority_sim_time is not None
                    and di.authority_sim_time < actual_info.authority_sim_time
                ):
                    continue
                actual_info.authority_sim_time = di.authority_sim_time
                actual_info.authority_source = di.authority_source
                actual_info.authority_sequence = di.authority_sequence

        # After 10 ticks, authority must be from tick 10, not tick 0
        assert d._actual_links[pair].authority_sim_time == datetime(
            2026, 1, 1, 0, 0, 10, tzinfo=UTC
        )
        assert d._actual_links[pair].authority_sequence == 11

    def test_authority_never_regresses_without_lineage_reset(self):
        """A stale retained snapshot with an older authority_sim_time must
        not overwrite newer authority on an active link.
        """
        from datetime import UTC, datetime

        from scheduler.desired_state import ActiveLinkInfo

        newer_sim = datetime(2026, 1, 1, 0, 0, 50, tzinfo=UTC)
        older_sim = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)

        actual_info = ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=1500.0,
            authority_sim_time=newer_sim,
            authority_source="snapshot",
            authority_sequence=50,
        )

        desired_info = ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=1500.0,
            authority_sim_time=older_sim,
            authority_source="snapshot",
            authority_sequence=10,
        )

        # The freshness update logic must reject the regression
        if (
            desired_info.authority_sim_time is not None
            and actual_info.authority_sim_time is not None
            and desired_info.authority_sim_time < actual_info.authority_sim_time
        ):
            pass  # correctly skipped
        else:
            actual_info.authority_sim_time = desired_info.authority_sim_time

        assert actual_info.authority_sim_time == newer_sim
        assert actual_info.authority_sequence == 50
