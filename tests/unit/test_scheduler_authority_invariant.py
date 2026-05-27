# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""C-A subset invariant repro test (Phase 1.4).

The Scheduler safety net at `dispatcher.py:_apply_events_to_desired`
(today around lines 926-932) retains a ground pair in `_desired_links`
when:
- OME emits a release event for the pair: `vis(visible=True,
  scheduled=False)` after MBB teardown elapsed.
- The pair was previously in teardown (`pair in self._teardown_pairs`).
- The Scheduler's `_actual_links` shows no OTHER active ground link
  on the same GS (the replacement BatchLinkUp ACK has not landed yet
  or returned failure).

This was added defensively to prevent the GS from being orphaned
without ground connectivity during a dispatch race. It is a
documented violation of the OME authority and scheduler fail-loud contracts
(no special cases hiding owning-component bugs).

Phase 1.4 introduces a PASSIVE divergence detector: `_ome_view` and
`authority_subset_violation()`. This test reproduces the failed-
replacement scenario and asserts the divergence is observable —
WITHOUT changing production behavior. Phase 5 of the foundations plan
removes the override and adds the fail-loud actuator path; at that
point `authority_subset_violation()` graduates to a production
`RuntimeError`.

The test pins TWO invariants:

1. **Current (broken) behavior**: after the failed-replacement event
   sequence, the override fires and the old pair survives in
   `_desired_links`. (Sub-phase 1.4 does NOT fix this.)

2. **Divergence is observable via `_ome_view`**: `_ome_view` correctly
   reflects the OME's stated `(visible=True, scheduled=False)` for
   the released pair, so `authority_subset_violation()` returns the
   pair. (Sub-phase 1.4 DOES provide this observation.)

When Phase 5 lands, this file's assertions flip: the override is
gone, `_desired_links` correctly excludes the released pair, and
`authority_subset_violation()` stays green.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from nodalarc.models.events import VisibilityEvent
from nodalarc.proto import node_agent_pb2
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap

SIM_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def _make_vis(
    pair: tuple[str, str],
    visible: bool,
    scheduled: bool,
    *,
    sim_time: datetime,
    scheduling_state: str = "active",
    link_type: str = "ground",
) -> VisibilityEvent:
    """Build a ground VisibilityEvent for the repro scenario."""
    return VisibilityEvent(
        sim_time=sim_time,
        node_a=pair[0],
        node_b=pair[1],
        visible=visible,
        scheduled=scheduled,
        range_km=900.0,
        latency_ms=3.0,
        elevation_deg=45.0,
        terminal_type="rf",
        link_type=link_type,
        gs_terminal_index=0 if link_type == "ground" else None,
        sat_terminal_index=0 if link_type == "ground" else None,
        scheduling_state=scheduling_state,
    )


def _make_dispatcher_with_two_terminal_gs() -> Dispatcher:
    """Construct a dispatcher with one 2-terminal GS that has MBB
    capacity for the failed-replacement repro."""
    interface_map = {
        ("gs-multi", "sat-old"): ("term0", "gnd0"),
        ("gs-multi", "sat-new"): ("term1", "gnd0"),
    }
    bandwidth_map = dict.fromkeys(interface_map, 1000.0)

    loc = PodLocationMap()
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "127.0.0.1:50100"

    pool = MagicMock()
    mock_stub = MagicMock()

    def up_resp(req):
        return node_agent_pb2.BatchLinkUpResponse(
            success=True,
            error_message="",
            interfaces_upped=len(req.interfaces),
            apply_time_ms=0.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                    verified=True,
                )
                for iface in req.interfaces
            ],
        )

    def down_resp(req):
        return node_agent_pb2.BatchLinkDownResponse(
            success=True,
            error_message="",
            interfaces_downed=len(req.interfaces),
            apply_time_ms=0.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                    verified=True,
                )
                for iface in req.interfaces
            ],
        )

    mock_stub.async_batch_link_up = AsyncMock(side_effect=up_resp)
    mock_stub.async_batch_link_down = AsyncMock(side_effect=down_resp)
    mock_stub.async_set_latency = AsyncMock(
        return_value=node_agent_pb2.SetLatencyResponse(success=True)
    )
    pool.get_stub.return_value = mock_stub

    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-c-a-repro",
        wiring_generation="sha256:" + "b" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={"gs-multi": 2},
        sat_ground_terminal_capacities={"sat-old": 1, "sat-new": 1},
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


def _old_active_info() -> ActiveLinkInfo:
    """The state we pre-seed for the old (incumbent) pair as if it had
    been previously dispatched and ACKed by the Node Agent."""
    return ActiveLinkInfo(
        interface_a="term0",
        interface_b="gnd0",
        latency_ms=3.0,
        bandwidth_mbps=1000.0,
        link_type="ground",
        range_km=900.0,
        authority_sim_time=SIM_T0,
        authority_source="visibility_event",
    )


class TestCAReproFailedReplacement:
    """Reproduces the safety-net override scenario.

    Setup:
      - 2-terminal GS with MBB enabled.
      - Old pair was scheduled, then entered teardown overlap.
      - Replacement (new pair) was NEVER ACKed (BatchLinkUp failed
        upstream — modeled by simply not adding it to _actual_links).
      - OME's teardown timer elapses; OME emits release event for the
        old pair: vis(visible=True, scheduled=False).

    Expected divergence:
      - _desired_links: contains old pair (safety net kept it alive).
      - _ome_view: marks old pair as (visible=True, scheduled=False).
      - authority_subset_violation() returns {old_pair}.
    """

    def _drive_failed_replacement(self) -> Dispatcher:
        """Reusable scenario driver. Returns the dispatcher in the
        diverged state for assertions."""
        d = _make_dispatcher_with_two_terminal_gs()
        old_pair = ("gs-multi", "sat-old")

        # Pre-state: the old pair is in _desired_links, marked teardown,
        # and the Scheduler has it as ACKed in _actual_links. The
        # replacement has NOT been ACKed yet (modeling the failed
        # replacement BatchLinkUp).
        d._desired_links[old_pair] = _old_active_info()
        d._actual_links[old_pair] = _old_active_info()
        d._teardown_pairs.add(old_pair)

        # Seed _ome_view to match: OME knows the old pair was in
        # teardown last tick. The teardown event from a previous step
        # would have populated this via _apply_events_to_desired.
        d._ome_view[old_pair] = (True, True, "teardown")

        # Now drive the release event: OME's teardown window elapsed,
        # OME has released the old pair. The new pair is "scheduled"
        # in OME's view but was never confirmed UP in the Scheduler.
        release_event = _make_vis(
            old_pair,
            visible=True,
            scheduled=False,
            sim_time=SIM_T0 + timedelta(seconds=3),
            scheduling_state="active",
        )
        d._apply_events_to_desired([release_event])
        return d

    def test_safety_net_retains_old_pair_in_desired_links(self) -> None:
        """Phase 1.4 must NOT change production behavior. The safety
        net at lines 926-932 of dispatcher.py fires and keeps the old
        pair alive in _desired_links. (Phase 5 removes this.)"""
        d = self._drive_failed_replacement()
        old_pair = ("gs-multi", "sat-old")
        assert old_pair in d._desired_links, (
            "Phase 1.4 must preserve the current safety-net behavior. "
            "If this assertion FLIPS, the override has been removed — "
            "verify the foundations-plan Phase 5 work also delivered "
            "the fail-loud actuator path."
        )

    def test_ome_view_reflects_ome_authority_release(self) -> None:
        """The OME's stated truth (visible=True, scheduled=False)
        propagates into _ome_view via the VisibilityEvent. This is
        what the divergence detector reads."""
        d = self._drive_failed_replacement()
        old_pair = ("gs-multi", "sat-old")
        assert old_pair in d._ome_view, "_ome_view must record the release event"
        visible, scheduled, sched_state = d._ome_view[old_pair]
        assert visible is True, "OME said the pair is still visible"
        assert scheduled is False, "OME said the pair is no longer scheduled"

    def test_authority_subset_violation_surfaces_divergence(self) -> None:
        """The Phase 1.4 observation: divergence between _desired_links
        and _ome_view is detectable. Phase 5 promotes this to a
        production RuntimeError."""
        d = self._drive_failed_replacement()
        old_pair = ("gs-multi", "sat-old")
        violations = d.authority_subset_violation()
        assert old_pair in violations, (
            "C-A repro: authority_subset_violation() must surface the "
            "pair retained by the safety net. _desired_links={pair}, "
            "_ome_view says scheduled=False. The divergence is the "
            "exact case the override hides; this test proves it is "
            "observable from outside the Scheduler."
        )

    def test_warning_log_is_emitted_for_audit_trail(self, caplog) -> None:
        """The override path logs a WARNING. Operators searching logs
        for 'MBB teardown blocked' must find an entry. This is the
        diagnostic surface that exists TODAY; the C-A repro pins it
        so a silent regression of the log line is caught."""
        import logging

        with caplog.at_level(logging.WARNING):
            self._drive_failed_replacement()
        warnings = [r for r in caplog.records if "MBB teardown blocked" in r.message]
        assert len(warnings) >= 1, (
            "The override must log a WARNING — 'MBB teardown blocked' — "
            "so operators can grep production logs for the divergence "
            "rate before Phase 5 lands. Silent override is the worst "
            "outcome."
        )


class TestAuthoritySubsetInvariantHappyPath:
    """Sanity check: the C-A observation does NOT fire false positives
    on healthy scenarios. The Scheduler keeps _desired_links and
    _ome_view in sync when no override is in play."""

    def test_no_violation_when_desired_matches_ome_view(self) -> None:
        d = _make_dispatcher_with_two_terminal_gs()
        pair = ("gs-multi", "sat-old")

        # Drive a normal up event.
        up_event = _make_vis(
            pair,
            visible=True,
            scheduled=True,
            sim_time=SIM_T0,
            scheduling_state="active",
        )
        d._apply_events_to_desired([up_event])

        assert pair in d._desired_links
        assert d._ome_view[pair] == (True, True, "active")
        assert d.authority_subset_violation() == set()

    def test_no_violation_when_link_correctly_dropped_on_visibility_loss(
        self,
    ) -> None:
        """A clean visibility loss: OME says not visible, the Scheduler
        drops the pair from _desired_links. No override fires."""
        d = _make_dispatcher_with_two_terminal_gs()
        pair = ("gs-multi", "sat-old")

        up_event = _make_vis(
            pair,
            visible=True,
            scheduled=True,
            sim_time=SIM_T0,
            scheduling_state="active",
        )
        d._apply_events_to_desired([up_event])
        assert pair in d._desired_links

        # Visibility loss event.
        loss_event = _make_vis(
            pair,
            visible=False,
            scheduled=False,
            sim_time=SIM_T0 + timedelta(seconds=10),
        )
        d._apply_events_to_desired([loss_event])

        assert pair not in d._desired_links, "Visibility loss must drop the pair"
        assert d._ome_view[pair] == (False, False, "active")
        assert d.authority_subset_violation() == set()
