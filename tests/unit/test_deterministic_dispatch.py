# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Prove deterministic dispatch and allocation ordering.

Two runs with identical inputs must produce identical output regardless of
Python hash seed. These tests construct inputs where multiple pairs compete
for the same resource and assert that the winner is always the same.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_allocator import allocate_ground_links
from ome.visibility import GroundVisibility


def _policy_kwargs(gs_id: str) -> dict:
    return {
        "gs_selection_policies": {gs_id: SelectionPolicySpec(name="highest-elevation")},
        "gs_handover_policies": {
            gs_id: HandoverPolicySpec(name="hysteresis", params=HysteresisParameters().model_dump())
        },
        "ranking_order": ("service_priority", "selection_score", "lex_pair"),
        "handover_mode": "bbm",
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
            GroundVisibility(
                sat_id=sat_a,
                visible=True,
                elevation_deg=45.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
            GroundVisibility(
                sat_id=sat_b,
                visible=True,
                elevation_deg=45.0,
                range_km=1000.0,
                remaining_visible_s=None,
                reject_reason="ok",
            ),
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
                sat_ground_terminal_indices_by_body=_sat_body_pools({sat_a: 1, sat_b: 1}),
                **_policy_kwargs(gs_id),
                gs_min_elevations={gs_id: 25.0},
                gs_service_priorities={gs_id: 10},
                gs_tenant_ids={gs_id: "default"},
                gs_reference_bodies={gs_id: "earth"},
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

        result = allocate_ground_links(
            step=0,
            visible_per_station={gs_id: visible},
            ground_station_ids={gs_id},
            current_associations={},
            pending_teardowns={},
            gs_terminal_counts={gs_id: 1},
            sat_ground_terminals={sat_a: 1, sat_b: 1},
            sat_ground_terminal_indices_by_body=_sat_body_pools({sat_a: 1, sat_b: 1}),
            **_policy_kwargs(gs_id),
            gs_min_elevations={gs_id: 25.0},
            gs_service_priorities={gs_id: 10},
            gs_tenant_ids={gs_id: "default"},
            gs_reference_bodies={gs_id: "earth"},
            mbb_overlap_ticks=3,
            mbb_reserve=0,
        )

        # (gs-A, sat-P00S00) < (gs-A, sat-P01S00) lexicographically
        expected_pair = (min(gs_id, sat_a), max(gs_id, sat_a))
        assert expected_pair in result.scheduled_pairs


class TestAuthorityFreshnessOnStableLinks:
    """Active-link authority metadata must be refreshed on every accepted
    snapshot, even when numeric values (range_km, latency_ms) are unchanged.

    These tests drive the real _reconcile_links production path, not an
    inline copy of the refresh logic.
    """

    @staticmethod
    def _make_dispatcher():
        from unittest.mock import MagicMock

        from scheduler.dispatcher import Dispatcher
        from scheduler.pod_locator import PodLocationMap

        pair = ("sat-P00S00", "sat-P00S01")
        iface_map = {pair: ("isl0", "isl1")}
        bw_map = {pair: 1000.0}
        loc = PodLocationMap()
        pool = MagicMock()
        pool.set_nc = MagicMock()

        d = Dispatcher(
            interface_map=iface_map,
            bandwidth_map=bw_map,
            pod_locator=loc,
            agent_pool=pool,
            session_id="test",
            wiring_generation="sha256:" + "a" * 64,
            gs_terminal_capacities={},
            sat_ground_terminal_capacities={},
            max_latency_age_s=2.0,
        )
        return d, pair

    def test_stable_link_authority_advances_via_reconcile_links(self):
        """_reconcile_links with identical range/latency but newer authority
        must update _actual_links authority metadata. This is the production
        path — the early return for no-change must NOT skip the refresh.
        """
        from datetime import UTC, datetime

        from scheduler.desired_state import ActiveLinkInfo

        d, pair = self._make_dispatcher()

        initial_sim = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        d._actual_links[pair] = ActiveLinkInfo(
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

        tick_sim = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
        desired = {
            pair: ActiveLinkInfo(
                interface_a="isl0",
                interface_b="isl1",
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                link_type="isl",
                range_km=1500.0,
                authority_sim_time=tick_sim,
                authority_source="snapshot",
                authority_sequence=2,
            )
        }

        asyncio.run(d._reconcile_links(desired, None, tick_sim))

        assert d._actual_links[pair].authority_sim_time == tick_sim
        assert d._actual_links[pair].authority_sequence == 2

    def test_authority_never_regresses_via_reconcile_links(self):
        """_reconcile_links with older authority must not overwrite newer
        authority on an active link.
        """
        from datetime import UTC, datetime

        from scheduler.desired_state import ActiveLinkInfo

        d, pair = self._make_dispatcher()

        newer_sim = datetime(2026, 1, 1, 0, 0, 50, tzinfo=UTC)
        d._actual_links[pair] = ActiveLinkInfo(
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

        older_sim = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
        desired = {
            pair: ActiveLinkInfo(
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
        }

        asyncio.run(d._reconcile_links(desired, None, older_sim))

        assert d._actual_links[pair].authority_sim_time == newer_sim
        assert d._actual_links[pair].authority_sequence == 50


class TestActuatorEventPublicationOrder:
    """send_batch_down and send_batch_up must publish NATS events in sorted
    pair order regardless of set insertion order. This proves determinism
    at the wire boundary, not just inside the Dispatcher.
    """

    @staticmethod
    def _locator():
        from nodalarc.proto import node_agent_pb2

        class _Loc:
            def link_locality(self, _a, _b):
                return node_agent_pb2.LOCALITY_LOCAL

            def agent_addr(self, node_id):
                return "agent-local"

            def k3s_node(self, node_id):
                return "k3s-local"

            def node_ip(self, k3s_node):
                return "10.0.0.1"

        return _Loc()

    @staticmethod
    def _mock_pool():
        """Agent pool whose stubs report all-success for any batch."""
        from nodalarc.proto import node_agent_pb2

        pool = MagicMock()

        async def _batch_down(req):
            resp = node_agent_pb2.BatchLinkDownResponse(
                success=True,
                interfaces_downed=len(req.interfaces),
                apply_time_ms=1.0,
            )
            for iface in req.interfaces:
                resp.interface_results.append(
                    node_agent_pb2.InterfaceResult(
                        node_id=iface.node_id,
                        interface_name=iface.interface_name,
                        success=True,
                        verified=True,
                    )
                )
            return resp

        async def _batch_up(req):
            resp = node_agent_pb2.BatchLinkUpResponse(
                success=True,
                interfaces_upped=len(req.interfaces),
                apply_time_ms=1.0,
            )
            for iface in req.interfaces:
                resp.interface_results.append(
                    node_agent_pb2.InterfaceResult(
                        node_id=iface.node_id,
                        interface_name=iface.interface_name,
                        success=True,
                        verified=True,
                    )
                )
            return resp

        stub = MagicMock()
        stub.async_batch_link_down = MagicMock(side_effect=_batch_down)
        stub.async_batch_link_up = MagicMock(side_effect=_batch_up)
        pool.get_stub = MagicMock(return_value=stub)
        return pool

    def test_send_batch_down_publishes_events_in_sorted_order(self):
        """Pass an unordered set of pairs to send_batch_down and verify
        LinkDown events are published in lexicographic pair order.
        """

        from scheduler.desired_state import ActiveLinkInfo
        from scheduler.dispatch_actuator import send_batch_down

        pair_c = ("sat-P02S00", "sat-P02S01")
        pair_a = ("sat-P00S00", "sat-P00S01")
        pair_b = ("sat-P01S00", "sat-P01S01")

        def _info(iface_a, iface_b):
            return ActiveLinkInfo(
                interface_a=iface_a,
                interface_b=iface_b,
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                link_type="isl",
                range_km=1500.0,
            )

        actual = {
            pair_c: _info("isl0", "isl1"),
            pair_a: _info("isl0", "isl1"),
            pair_b: _info("isl0", "isl1"),
        }

        published_pairs: list[tuple[str, str]] = []
        js = MagicMock()

        async def _capture_publish(subject, payload):
            data = json.loads(payload)
            published_pairs.append((data["node_a"], data["node_b"]))

        js.publish = MagicMock(side_effect=_capture_publish)

        sim_time = datetime(2026, 1, 1, tzinfo=UTC)
        # Deliberately unordered set
        pairs = {pair_c, pair_a, pair_b}

        asyncio.run(
            send_batch_down(
                pairs=pairs,
                actual_links=actual,
                locator=self._locator(),
                pool=self._mock_pool(),
                js=js,
                subj_link_down="test.link.down",
                sim_iso=sim_time.isoformat(),
                sim_time=sim_time,
                down_reasons={},
                gs_capacities={},
                session_id="test-session",
                wiring_generation="test-generation",
            )
        )

        assert published_pairs == [pair_a, pair_b, pair_c]

    def test_send_batch_up_publishes_events_in_sorted_order(self):
        """Pass an unordered set of pairs to send_batch_up and verify
        LinkUp events are published in lexicographic pair order.
        """

        from scheduler.desired_state import ActiveLinkInfo
        from scheduler.dispatch_actuator import send_batch_up
        from scheduler.latency_compensator import LatencyCompensation

        pair_c = ("sat-P02S00", "sat-P02S01")
        pair_a = ("sat-P00S00", "sat-P00S01")
        pair_b = ("sat-P01S00", "sat-P01S01")

        sim_time = datetime(2026, 1, 1, tzinfo=UTC)

        def _info(iface_a, iface_b):
            return ActiveLinkInfo(
                interface_a=iface_a,
                interface_b=iface_b,
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                link_type="isl",
                range_km=1500.0,
                authority_sim_time=sim_time,
                authority_source="snapshot",
                authority_sequence=1,
            )

        desired = {
            pair_c: _info("isl0", "isl1"),
            pair_a: _info("isl0", "isl1"),
            pair_b: _info("isl0", "isl1"),
        }

        published_pairs: list[tuple[str, str]] = []
        js = MagicMock()

        async def _capture_publish(subject, payload):
            data = json.loads(payload)
            published_pairs.append((data["node_a"], data["node_b"]))

        js.publish = MagicMock(side_effect=_capture_publish)

        def _compensation(_a, _b, orbital_ms):
            return LatencyCompensation(
                orbital_one_way_ms=orbital_ms,
                substrate_rtt_ms=0.0,
                substrate_one_way_ms=0.0,
                netem_one_way_ms=orbital_ms,
                rtt_to_one_way_policy="half-rtt",
            )

        def _noop_freshness(*_args, **_kwargs):
            pass

        def _noop_provenance(*_args, **_kwargs):
            return None

        pairs = {pair_c, pair_a, pair_b}

        asyncio.run(
            send_batch_up(
                pairs=pairs,
                desired=desired,
                locator=self._locator(),
                pool=self._mock_pool(),
                js=js,
                subj_link_up="test.link.up",
                sim_iso=sim_time.isoformat(),
                sim_time=sim_time,
                gs_capacities={},
                latency_compensation=_compensation,
                validate_authority_freshness=_noop_freshness,
                link_provenance=_noop_provenance,
                session_id="test-session",
                wiring_generation="test-generation",
            )
        )

        assert published_pairs == [pair_a, pair_b, pair_c]
