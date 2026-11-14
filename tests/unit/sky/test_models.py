# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Test that every Pydantic model round-trips through JSON serialization.

Proves the contract that NATS messages, SQLite records, and config
files depend on. If a model round-trip fails, components that serialize
and deserialize will disagree on the data.
"""

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import (
    VisibilityEvent,
)
from nodalarc.models.vs_api import (
    TracedPath,
)
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


# --- events.py ---


class TestVisibilityEvent:
    def test_node_ordering_enforced(self):
        """node_a must be alphabetically < node_b; validator swaps if needed."""
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="sat-P01S00",
            node_b="sat-P00S00",
            visible=True,
            scheduled=True,
            range_km=1000.0,
            elevation_deg=None,
            terminal_type="optical",
            visibility_reject_reason="ok",
            unscheduled_reason=None,
        )
        assert evt.node_a == "sat-P00S00"
        assert evt.node_b == "sat-P01S00"

    @pytest.mark.parametrize(
        ("assigned_terminals", "given_order"),
        [
            (False, ("sat-P00S00", "sat-P01S00")),
            (False, ("sat-P01S00", "sat-P00S00")),
            (True, ("gs-den", "sat-P00S00")),
            (True, ("sat-P00S00", "gs-den")),
        ],
        ids=["fixed-sorted", "fixed-reversed", "assigned-sorted", "assigned-reversed"],
    )
    def test_each_delay_stays_with_its_transmitting_end(self, assigned_terminals, given_order):
        """However the producer orders the nodes, the event's a_to_b is the delay of the signal
        its node_a sends and b_to_a the one its node_b sends, and a JSON round trip keeps
        them there."""
        first, second = given_order
        sent_by = {first: 7.4121, second: 7.4127}
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a=first,
            node_b=second,
            visible=True,
            scheduled=True,
            range_km=2222.0,
            latency_a_to_b_ms=sent_by[first],
            latency_b_to_a_ms=sent_by[second],
            elevation_deg=40.0 if assigned_terminals else None,
            terminal_index_a=0 if assigned_terminals else None,
            terminal_index_b=0 if assigned_terminals else None,
            terminal_type="rf" if assigned_terminals else "optical",
            visibility_reject_reason="ok",
            unscheduled_reason=None,
        )

        assert evt.node_a < evt.node_b
        for stored in (evt, VisibilityEvent.model_validate_json(evt.model_dump_json())):
            assert stored.latency_a_to_b_ms == sent_by[stored.node_a]
            assert stored.latency_b_to_a_ms == sent_by[stored.node_b]

    def test_visibility_reject_reason_required(self):
        """No default. Producer MUST declare physical state."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="visibility_reject_reason"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                # visibility_reject_reason omitted — required field absent.
                unscheduled_reason=None,
            )

    def test_visible_false_with_ok_reject_reason_rejected(self):
        """Invisible event with reject_reason='ok' is impossible —
        validator must reject it."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="non-'ok'"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=False,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="ok",
                unscheduled_reason=None,
            )

    def test_visible_true_with_non_ok_reject_reason_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="visible=True requires"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
                unscheduled_reason=None,
            )

    def test_unscheduled_reason_on_scheduled_pair_rejected(self):
        """A scheduled pair has no unscheduled reason."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="unscheduled_reason set on a scheduled"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="ok",
                unscheduled_reason="isl_terminal_capacity",
            )

    def test_unscheduled_reason_on_invisible_pair_rejected(self):
        """An invisible pair never reached the allocator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="unscheduled_reason set on a non-visible"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=False,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
                unscheduled_reason="isl_terminal_capacity",
            )

    def test_visible_unscheduled_without_reason_rejected(self):
        """A visible-but-unscheduled event with unscheduled_reason=None
        is a producer bug — the allocator must attribute every visible
        pair it did not schedule. Refuse construction so the producer
        cannot silently emit an unexplainable transition."""
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="visible=True with scheduled=False requires",
        ):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=True,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="ok",
                unscheduled_reason=None,
            )

    def test_invisible_but_scheduled_rejected(self):
        """visible=False with scheduled=True is impossible — a pair the
        OME deemed invisible cannot also be in the allocator's
        scheduled set."""
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="visible=False with scheduled=True is impossible",
        ):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=False,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
                unscheduled_reason=None,
            )


# --- metrics.py ---


# --- vs_api.py ---


class TestTracedPath:
    @staticmethod
    def _reached(**overrides) -> dict:
        fields = {
            "continuous_trace": True,
            "src_node": "gs-ashburn",
            "dst_node": "gs-frankfurt",
            "hops": ["gs-ashburn", "sat-P02S05", "gs-frankfurt"],
            "hop_rtts": [None, 5.0, 12.0],
            "state": "reached",
            "rtt_ms": 12.0,
            "error": None,
            "reverse_hops": ["gs-frankfurt", "sat-P02S05", "gs-ashburn"],
            "reverse_hop_rtts": [None, 6.0, 12.5],
            "reverse_state": "reached",
            "reverse_rtt_ms": 12.5,
            "reverse_error": None,
            "asymmetry_detected": False,
            "tracing": True,
            "traced_at": "2026-09-23T00:00:00+00:00",
            "sim_time": "2026-06-08T00:00:00+00:00",
            "measured_from": "2026-09-23T00:00:00+00:00",
            "measured_until": "2026-09-23T00:00:04+00:00",
            "wall_clock": {"representative": None, "regimes": []},
        }
        return {**fields, **overrides}

    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"state": "not_reached"}, "rtt_ms exists only when it reached"),
            ({"rtt_ms": None}, "rtt_ms exists only when it reached"),
            (
                {"reverse_state": "failed", "reverse_rtt_ms": None},
                "error exists only when it failed",
            ),
            ({"hop_rtts": [None, 5.0]}, "3 hops and 2 round trips"),
            (
                {"reverse_state": "not_reached", "reverse_rtt_ms": None},
                "asymmetry is known only when both directions reached",
            ),
        ],
    )
    def test_outcomes_must_agree_with_their_states(self, overrides, match):
        with pytest.raises(ValidationError, match=match):
            TracedPath(**self._reached(**overrides))
