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
from nodalarc.models.link_events import (
    LinkDown,
    LinkUp,
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
            link_type="isl",
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

    def test_visibility_reject_reason_required(self):
        """No default. Producer MUST declare physical state."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="visibility_reject_reason"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
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
                link_type="isl",
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
                link_type="isl",
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
                link_type="isl",
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
                link_type="isl",
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
                link_type="isl",
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
                link_type="isl",
                visible=False,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
                unscheduled_reason=None,
            )

    def test_ground_event_rejects_isl_only_reject_reason(self):
        """A ground event stamped with an ISL-only physical reason is
        impossible — the ground physics gate never emits those values."""
        from pydantic import ValidationError

        for isl_only in (
            "polar_seam",
            "terminal_type_mismatch",
            "terminal_role_mismatch",
        ):
            with pytest.raises(
                ValidationError, match="link_type='ground' rejects visibility_reject_reason"
            ):
                VisibilityEvent(
                    sim_time=NOW,
                    node_a="gs-den",
                    node_b="sat-P00S00",
                    link_type="ground",
                    visible=False,
                    scheduled=False,
                    range_km=1000.0,
                    elevation_deg=10.0,
                    terminal_type="rf",
                    visibility_reject_reason=isl_only,
                    unscheduled_reason=None,
                )

    def test_ground_event_rejects_isl_only_unscheduled_reason(self):
        """``isl_terminal_capacity`` is satellite-side ISL allocator state
        and cannot describe a ground rejection."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="link_type='ground' rejects unscheduled_reason"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="gs-den",
                node_b="sat-P00S00",
                link_type="ground",
                visible=True,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=40.0,
                terminal_type="rf",
                visibility_reject_reason="ok",
                unscheduled_reason="isl_terminal_capacity",
            )

    def test_isl_event_rejects_ground_only_reject_reason(self):
        """ISL terminals have no ground elevation mask;
        ``elevation_below_min`` is a ground-only physical reason."""
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError, match="link_type='isl' rejects visibility_reject_reason"
        ):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
                visible=False,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="elevation_below_min",
                unscheduled_reason=None,
            )

    def test_isl_event_rejects_ground_only_unscheduled_reason(self):
        """Ground allocator reasons (gs_capacity, sat_capacity, etc.)
        cannot describe an ISL rejection."""
        from pydantic import ValidationError

        for ground_only in (
            "gs_capacity",
            "sat_capacity",
            "hysteresis_hold",
            "incumbent_held",
            "bbm_no_spare",
            "replaced_by_successor",
        ):
            with pytest.raises(ValidationError, match="link_type='isl' rejects unscheduled_reason"):
                VisibilityEvent(
                    sim_time=NOW,
                    node_a="sat-P00S00",
                    node_b="sat-P00S01",
                    link_type="isl",
                    visible=True,
                    scheduled=False,
                    range_km=1000.0,
                    elevation_deg=None,
                    terminal_type="optical",
                    visibility_reject_reason="ok",
                    unscheduled_reason=ground_only,
                )

    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
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
                unscheduled_reason=None,
            )


# --- link_events.py ---


class TestLinkUp:
    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
            LinkUp(
                sim_time=NOW,
                wall_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                interface_a="isl0",
                interface_b="isl1",
                latency_ms=5.0,
                range_km=1500.0,
                reason="vis_gained",
            )


class TestLinkDown:
    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
            LinkDown(
                sim_time=NOW,
                wall_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                interface_a="isl0",
                interface_b="isl1",
                reason="vis_lost",
            )


# --- metrics.py ---


# --- vs_api.py ---


class TestTracedPath:
    @staticmethod
    def _reached(**overrides) -> dict:
        fields = {
            "flow_id": "ashburn-to-frankfurt",
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
