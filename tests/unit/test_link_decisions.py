# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for link-decision boundary types.

These tests assert the strictness contract: every field is required at
construction, no permissive defaults, body and tenant scope are
mandatory. Tests deliberately try to construct invalid instances and
assert that construction raises.

This is Phase 1.1 of the foundational trust plan: the types exist and
are strict; consumers migrate in Phase 1.2.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from nodalarc.models.link_decisions import (
    GroundLinkDecisionSnapshot,
    GroundVisibilityDecisionWire,
    UnscheduledPair,
)
from nodalarc.nats_channels import (
    SUBJECT_GROUND_LINK_DECISION_SNAPSHOT,
    SUBJECT_LINK_STATE_SNAPSHOT,
    ground_link_decision_snapshot_subject,
)
from ome.types import GroundVisibilityDecision
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision_kwargs_wire() -> dict:
    """Minimum valid kwargs to construct a wire decision."""
    return {
        "pair": ("gs-a", "sat-1"),
        "tenant_id": "default",
        "reference_body": "earth",
        "visible": True,
        "range_km": 1234.5,
        "elevation_deg": 42.0,
        "azimuth_deg": 180.0,
        "observer_frame": "body_local",
        "reject_reason": "ok",
        "applied_min_elevation_deg": 25.0,
        "applied_max_range_km": 2000.0,
        "applied_field_of_regard_deg": 120.0,
        "applied_max_tracking_rate_deg_s": 4.0,
        "applied_boresight_mode": "local_vertical",
        "applied_gs_terminal_profile": "gs_ka_gateway",
        "applied_sat_terminal_profile": "sat_ka_gateway",
    }


def _decision_kwargs_hot() -> dict:
    """Minimum valid kwargs to construct the hot-path dataclass."""
    return _decision_kwargs_wire()


def _unscheduled_kwargs() -> dict:
    return {
        "pair": ("gs-a", "sat-2"),
        "tenant_id": "default",
        "reference_body": "earth",
        "unscheduled_reason": "gs_capacity",
    }


# ---------------------------------------------------------------------------
# Hot-path dataclass (services/ome/types.py)
# ---------------------------------------------------------------------------


class TestGroundVisibilityDecisionHotPath:
    """The hot-path slotted dataclass form used inside OME compute."""

    def test_minimum_valid_construction(self) -> None:
        d = GroundVisibilityDecision(**_decision_kwargs_hot())
        assert d.pair == ("gs-a", "sat-1")
        assert d.tenant_id == "default"
        assert d.reference_body == "earth"
        assert d.visible is True
        assert d.reject_reason == "ok"

    def test_dataclass_is_frozen(self) -> None:
        d = GroundVisibilityDecision(**_decision_kwargs_hot())
        with pytest.raises(FrozenInstanceError):
            d.visible = False  # type: ignore[misc]

    def test_dataclass_uses_slots_no_dict(self) -> None:
        """Slotted dataclasses cannot grow new attributes; this is the
        zero-overhead invariant the hot path depends on.

        We verify the slots invariant via the absence of `__dict__`.
        Setting an unknown attribute on a frozen+slotted dataclass
        raises (Python ordering of frozen vs slots checks is an
        implementation detail), so we also verify rejection without
        pinning the specific exception class — the contract is "no
        new attributes allowed."
        """
        d = GroundVisibilityDecision(**_decision_kwargs_hot())
        assert not hasattr(d, "__dict__")
        with pytest.raises(Exception):  # noqa: B017,PT011 — see docstring
            d.unknown_field = "x"  # type: ignore[attr-defined]

    def test_every_field_is_required(self) -> None:
        """No permissive defaults. Drop any required field and construction must fail."""
        base = _decision_kwargs_hot()
        required_fields = [k for k in base if base[k] is not None or k.startswith("applied_")]
        # All non-Optional fields are required positionally; verify by
        # removing each one and confirming TypeError.
        always_required = [
            "pair",
            "tenant_id",
            "reference_body",
            "visible",
            "range_km",
            "elevation_deg",
            "observer_frame",
            "reject_reason",
            "applied_min_elevation_deg",
        ]
        for field in always_required:
            kwargs = dict(base)
            del kwargs[field]
            with pytest.raises(TypeError, match=field):
                GroundVisibilityDecision(**kwargs)
        # Sanity: optional-but-present field name was correctly classified
        assert "azimuth_deg" in required_fields

    def test_isl_only_reject_reason_rejected_on_hot_path(self) -> None:
        """Static `Literal` typing rules out ISL-only values, but
        dataclasses do not enforce it at runtime. The hot path must
        fail loud anyway so a producer bug cannot inject `polar_seam`
        or other ISL values into a ground decision in the compute loop."""
        for isl_only in (
            "polar_seam",
            "terminal_type_mismatch",
            "terminal_role_mismatch",
        ):
            kwargs = _decision_kwargs_hot()
            kwargs["visible"] = False
            kwargs["reject_reason"] = isl_only
            with pytest.raises(ValueError, match="not a valid ground rejection reason"):
                GroundVisibilityDecision(**kwargs)

    def test_none_applied_fields_allowed(self) -> None:
        """`applied_*` fields and `azimuth_deg` may be None — semantic
        meaning is 'constraint not applied for this decision'.

        The terminal profile fields may also be None when the decision
        was computed under `geometry_only` fidelity (no terminal
        constraint profile applied)."""
        kwargs = _decision_kwargs_hot()
        kwargs["azimuth_deg"] = None
        kwargs["applied_max_range_km"] = None
        kwargs["applied_field_of_regard_deg"] = None
        kwargs["applied_max_tracking_rate_deg_s"] = None
        kwargs["applied_boresight_mode"] = None
        kwargs["applied_gs_terminal_profile"] = None
        kwargs["applied_sat_terminal_profile"] = None
        d = GroundVisibilityDecision(**kwargs)
        assert d.azimuth_deg is None
        assert d.applied_max_range_km is None
        assert d.applied_field_of_regard_deg is None
        assert d.applied_max_tracking_rate_deg_s is None
        assert d.applied_boresight_mode is None
        assert d.applied_gs_terminal_profile is None
        assert d.applied_sat_terminal_profile is None


# ---------------------------------------------------------------------------
# Wire boundary type (lib/nodalarc/models/link_decisions.py)
# ---------------------------------------------------------------------------


class TestGroundVisibilityDecisionWire:
    """The Pydantic frozen wire form for NATS publish/parse."""

    def test_minimum_valid_construction(self) -> None:
        w = GroundVisibilityDecisionWire(**_decision_kwargs_wire())
        assert w.pair == ("gs-a", "sat-1")
        assert w.observer_frame == "body_local"

    def test_wire_is_frozen(self) -> None:
        w = GroundVisibilityDecisionWire(**_decision_kwargs_wire())
        with pytest.raises(ValidationError):
            w.visible = False  # type: ignore[misc]

    def test_invalid_reject_reason_rejected(self) -> None:
        kwargs = _decision_kwargs_wire()
        kwargs["reject_reason"] = "made_up_reason"
        with pytest.raises(ValidationError):
            GroundVisibilityDecisionWire(**kwargs)

    def test_isl_only_reject_reason_rejected_on_ground_decision(self) -> None:
        """ISL-only physical reasons cannot appear on a ground decision.
        Polar seam, terminal type mismatch, and terminal role mismatch
        are inter-satellite-only failure modes — stamping them on a
        ground decision is a producer bug, and the type system refuses
        it."""
        for isl_only in (
            "polar_seam",
            "terminal_type_mismatch",
            "terminal_role_mismatch",
        ):
            kwargs = _decision_kwargs_wire()
            kwargs["visible"] = False
            kwargs["reject_reason"] = isl_only
            with pytest.raises(ValidationError):
                GroundVisibilityDecisionWire(**kwargs)

    def test_invalid_observer_frame_rejected(self) -> None:
        kwargs = _decision_kwargs_wire()
        kwargs["observer_frame"] = "geocentric"
        with pytest.raises(ValidationError):
            GroundVisibilityDecisionWire(**kwargs)

    def test_invalid_boresight_mode_rejected(self) -> None:
        kwargs = _decision_kwargs_wire()
        kwargs["applied_boresight_mode"] = "made_up_mode"
        with pytest.raises(ValidationError):
            GroundVisibilityDecisionWire(**kwargs)

    def test_round_trip_json_preserves_fields(self) -> None:
        """The wire form must serialize and round-trip exactly. This
        guarantees NATS publish/parse preserves the decision payload."""
        original = GroundVisibilityDecisionWire(**_decision_kwargs_wire())
        payload = original.model_dump_json()
        parsed = GroundVisibilityDecisionWire.model_validate_json(payload)
        assert parsed == original

    def test_tenant_and_body_required(self) -> None:
        """Direction 2 + Direction 3: tenant_id and reference_body are
        mandatory. Omitting either MUST fail validation."""
        kwargs = _decision_kwargs_wire()
        del kwargs["tenant_id"]
        with pytest.raises(ValidationError, match="tenant_id"):
            GroundVisibilityDecisionWire(**kwargs)
        kwargs = _decision_kwargs_wire()
        del kwargs["reference_body"]
        with pytest.raises(ValidationError, match="reference_body"):
            GroundVisibilityDecisionWire(**kwargs)


# ---------------------------------------------------------------------------
# UnscheduledPair
# ---------------------------------------------------------------------------


class TestUnscheduledPair:
    def test_minimum_valid_construction(self) -> None:
        u = UnscheduledPair(
            **_unscheduled_kwargs(),
            incumbent_pair=None,
            capacity_constraint=None,
        )
        assert u.pair == ("gs-a", "sat-2")
        assert u.unscheduled_reason == "gs_capacity"
        assert u.incumbent_pair is None
        assert u.capacity_constraint is None

    def test_with_incumbent_and_constraint(self) -> None:
        u = UnscheduledPair(
            **_unscheduled_kwargs(),
            incumbent_pair=("gs-a", "sat-3"),
            capacity_constraint="sat-2.gnd0",
        )
        assert u.incumbent_pair == ("gs-a", "sat-3")
        assert u.capacity_constraint == "sat-2.gnd0"

    def test_unscheduled_pair_is_frozen(self) -> None:
        u = UnscheduledPair(
            **_unscheduled_kwargs(),
            incumbent_pair=None,
            capacity_constraint=None,
        )
        with pytest.raises(ValidationError):
            u.unscheduled_reason = "bbm_no_spare"  # type: ignore[misc]

    def test_invalid_reason_rejected(self) -> None:
        kwargs = _unscheduled_kwargs()
        kwargs["unscheduled_reason"] = "active_teardown"
        with pytest.raises(ValidationError):
            UnscheduledPair(
                **kwargs,
                incumbent_pair=None,
                capacity_constraint=None,
            )

    def test_isl_only_unscheduled_reason_rejected_on_ground_decision(self) -> None:
        """The ISL allocator's `isl_terminal_capacity` is a satellite-
        side resource exhaustion. It cannot describe a ground-link
        rejection. A `GroundLinkDecisionSnapshot` is ground-context, so
        `UnscheduledPair` refuses the ISL-only reason at construction."""
        kwargs = _unscheduled_kwargs()
        kwargs["unscheduled_reason"] = "isl_terminal_capacity"
        with pytest.raises(ValidationError):
            UnscheduledPair(
                **kwargs,
                incumbent_pair=None,
                capacity_constraint=None,
            )

    def test_teardown_explicitly_not_an_unscheduled_reason(self) -> None:
        """Teardown overlap is a scheduling state, not an
        unscheduled-reason. Any future maintainer who tries to add
        `mbb_pending_teardown` or `teardown` to the enum should hit
        this test."""
        for bad in ("mbb_pending_teardown", "teardown", "in_teardown"):
            kwargs = _unscheduled_kwargs()
            kwargs["unscheduled_reason"] = bad
            with pytest.raises(ValidationError):
                UnscheduledPair(
                    **kwargs,
                    incumbent_pair=None,
                    capacity_constraint=None,
                )


# ---------------------------------------------------------------------------
# GroundLinkDecisionSnapshot
# ---------------------------------------------------------------------------


class TestLinkDecisionSnapshot:
    def _snapshot_kwargs(self) -> dict:
        # Snapshot-level consistency: the unscheduled pair must
        # reference a visible decision in the SAME snapshot. Both
        # fixtures point at ("gs-a", "sat-1").
        unscheduled_for_decision = UnscheduledPair(
            pair=("gs-a", "sat-1"),
            tenant_id="default",
            reference_body="earth",
            unscheduled_reason="gs_capacity",
            incumbent_pair=None,
            capacity_constraint=None,
        )
        return {
            "sim_time": datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
            "snapshot_seq": 42,
            "epoch_id": 1,
            "decisions": (GroundVisibilityDecisionWire(**_decision_kwargs_wire()),),
            "unscheduled_pairs": (unscheduled_for_decision,),
        }

    def test_construction(self) -> None:
        s = GroundLinkDecisionSnapshot(**self._snapshot_kwargs())
        assert s.snapshot_seq == 42
        assert s.epoch_id == 1
        assert len(s.decisions) == 1
        assert len(s.unscheduled_pairs) == 1

    def test_empty_decisions_and_unscheduled_allowed(self) -> None:
        """A tick can produce no decisions (e.g., no ground stations).
        Empty tuples must construct successfully."""
        kwargs = self._snapshot_kwargs()
        kwargs["decisions"] = ()
        kwargs["unscheduled_pairs"] = ()
        s = GroundLinkDecisionSnapshot(**kwargs)
        assert s.decisions == ()
        assert s.unscheduled_pairs == ()

    def test_snapshot_is_frozen(self) -> None:
        s = GroundLinkDecisionSnapshot(**self._snapshot_kwargs())
        with pytest.raises(ValidationError):
            s.snapshot_seq = 43  # type: ignore[misc]

    def test_round_trip_json(self) -> None:
        original = GroundLinkDecisionSnapshot(**self._snapshot_kwargs())
        payload = original.model_dump_json()
        parsed = GroundLinkDecisionSnapshot.model_validate_json(payload)
        assert parsed == original

    def test_seq_and_epoch_must_be_present(self) -> None:
        """snapshot_seq and epoch_id correlate with `LinkStateSnapshot`
        of the same sim_time. Neither may be omitted."""
        for field in ("snapshot_seq", "epoch_id", "sim_time"):
            kwargs = self._snapshot_kwargs()
            del kwargs[field]
            with pytest.raises(ValidationError, match=field):
                GroundLinkDecisionSnapshot(**kwargs)


# ---------------------------------------------------------------------------
# Cross-type semantics
# ---------------------------------------------------------------------------


class TestVisibleRejectReasonConsistency:
    """Foundational consistency: visible iff reject_reason == 'ok'.
    Both forms must reject impossible states at construction."""

    def test_wire_rejects_visible_true_with_non_ok_reason(self) -> None:
        for bad in ("los_blocked", "elevation_below_min"):
            kwargs = _decision_kwargs_wire()
            kwargs["visible"] = True
            kwargs["reject_reason"] = bad
            # Terminal-bound rejection profiles default to set; clear so
            # the *visible* check fires first.
            with pytest.raises(ValidationError, match="visible=True requires"):
                GroundVisibilityDecisionWire(**kwargs)

    def test_wire_rejects_visible_false_with_ok_reason(self) -> None:
        kwargs = _decision_kwargs_wire()
        kwargs["visible"] = False
        kwargs["reject_reason"] = "ok"
        with pytest.raises(ValidationError, match="non-'ok' reject_reason"):
            GroundVisibilityDecisionWire(**kwargs)

    def test_hot_path_rejects_visible_true_with_non_ok_reason(self) -> None:
        kwargs = _decision_kwargs_hot()
        kwargs["visible"] = True
        kwargs["reject_reason"] = "los_blocked"
        with pytest.raises(ValueError, match="visible=True requires"):
            GroundVisibilityDecision(**kwargs)

    def test_hot_path_rejects_visible_false_with_ok_reason(self) -> None:
        kwargs = _decision_kwargs_hot()
        kwargs["visible"] = False
        kwargs["reject_reason"] = "ok"
        with pytest.raises(ValueError, match="non-'ok' reject_reason"):
            GroundVisibilityDecision(**kwargs)


class TestTerminalConstraintAttribution:
    """Terminal-bound rejections must name the terminal profile that
    rejected. The producer cannot say 'range_exceeded' without saying
    *which terminal's range*. Asserted on both the hot-path dataclass
    and the wire form."""

    TERMINAL_BOUND_REJECTIONS = (
        "range_exceeded",
        "field_of_regard",
        "tracking_exceeded",
    )

    @pytest.mark.parametrize("reason", TERMINAL_BOUND_REJECTIONS)
    def test_hot_path_rejection_without_profile_fails(self, reason: str) -> None:
        kwargs = _decision_kwargs_hot()
        kwargs["visible"] = False
        kwargs["reject_reason"] = reason
        kwargs["applied_gs_terminal_profile"] = None
        kwargs["applied_sat_terminal_profile"] = None
        with pytest.raises(ValueError, match="attributable to a specific terminal"):
            GroundVisibilityDecision(**kwargs)

    @pytest.mark.parametrize("reason", TERMINAL_BOUND_REJECTIONS)
    def test_wire_rejection_without_profile_fails(self, reason: str) -> None:
        kwargs = _decision_kwargs_wire()
        kwargs["visible"] = False
        kwargs["reject_reason"] = reason
        kwargs["applied_gs_terminal_profile"] = None
        kwargs["applied_sat_terminal_profile"] = None
        with pytest.raises(ValidationError):
            GroundVisibilityDecisionWire(**kwargs)

    @pytest.mark.parametrize("reason", TERMINAL_BOUND_REJECTIONS)
    def test_one_side_attribution_is_enough(self, reason: str) -> None:
        """If only one terminal's constraint was the cause, naming
        only that side is sufficient."""
        for side in ("applied_gs_terminal_profile", "applied_sat_terminal_profile"):
            kwargs = _decision_kwargs_wire()
            kwargs["visible"] = False
            kwargs["reject_reason"] = reason
            kwargs["applied_gs_terminal_profile"] = None
            kwargs["applied_sat_terminal_profile"] = None
            kwargs[side] = "named_profile"
            w = GroundVisibilityDecisionWire(**kwargs)
            assert getattr(w, side) == "named_profile"

    def test_non_terminal_rejections_do_not_require_profile(self) -> None:
        """`los_blocked` and `elevation_below_min` are physical
        rejections that do NOT depend on terminal constraints. They
        must work without a profile (e.g., a `geometry_only` session)."""
        for reason in ("los_blocked", "elevation_below_min"):
            kwargs = _decision_kwargs_wire()
            kwargs["visible"] = False
            kwargs["reject_reason"] = reason
            kwargs["applied_gs_terminal_profile"] = None
            kwargs["applied_sat_terminal_profile"] = None
            w = GroundVisibilityDecisionWire(**kwargs)
            assert w.applied_gs_terminal_profile is None
            assert w.applied_sat_terminal_profile is None


class TestCrossTypeSemantics:
    """Tests that pin the two-axis taxonomy: visibility-reject reasons
    are independent of unscheduled reasons."""

    def test_invisible_pair_has_reject_reason_not_ok(self) -> None:
        """A pair with `visible=False` MUST carry a non-`ok`
        reject_reason. This is enforced by the producer, but we pin
        the contract semantics here."""
        kwargs = _decision_kwargs_wire()
        kwargs["visible"] = False
        kwargs["reject_reason"] = "elevation_below_min"
        kwargs["elevation_deg"] = 10.0
        kwargs["applied_min_elevation_deg"] = 25.0
        w = GroundVisibilityDecisionWire(**kwargs)
        assert w.visible is False
        assert w.reject_reason != "ok"

    def test_visible_pair_typically_has_reject_reason_ok(self) -> None:
        """Mirror: a visible pair's reject_reason is `ok`."""
        w = GroundVisibilityDecisionWire(**_decision_kwargs_wire())
        assert w.visible is True
        assert w.reject_reason == "ok"

    def test_hot_path_and_wire_have_same_shape(self) -> None:
        """The slotted dataclass and the Pydantic wire model must
        accept the same kwargs. If they diverge, the publish-time
        conversion will silently lose fields."""
        kwargs = _decision_kwargs_wire()
        hot = GroundVisibilityDecision(**kwargs)
        wire = GroundVisibilityDecisionWire(**kwargs)
        for field in kwargs:
            assert getattr(hot, field) == getattr(wire, field), field


# ---------------------------------------------------------------------------
# NATS subject — pin the ground_link_decision_snapshot_subject builder
# ---------------------------------------------------------------------------


class TestLinkDecisionSnapshotSubject:
    """SUBJECT_GROUND_LINK_DECISION_SNAPSHOT lives on the NODALARC_LINKS
    stream (already MaxMsgsPerSubject=1). The subject pattern parallels
    ``link_state_snapshot_subject`` so both snapshots retain together
    per session; pairing between them is by (epoch_id, snapshot_seq,
    sim_time) in the consumer, not by shared-stream colocation."""

    def test_session_scoped_subject_pattern(self) -> None:
        subj = ground_link_decision_snapshot_subject("starlink-prod")
        assert subj == "nodalarc.links.starlink-prod.ground_decisions"

    def test_legacy_constant_uses_default_session(self) -> None:
        assert SUBJECT_GROUND_LINK_DECISION_SNAPSHOT == "nodalarc.links.default.ground_decisions"

    def test_decision_subject_lives_on_links_stream(self) -> None:
        """Both decision and state snapshots share the
        `nodalarc.links.{session}.*` namespace so they live on the
        NODALARC_LINKS stream's per-subject retention. Same stream is
        NOT pairing — pairing happens explicitly by
        (epoch_id, snapshot_seq, sim_time) in the consumer; see
        scheduler.dispatcher.paired_decision_snapshot."""
        assert SUBJECT_LINK_STATE_SNAPSHOT.startswith("nodalarc.links.")
        assert SUBJECT_GROUND_LINK_DECISION_SNAPSHOT.startswith("nodalarc.links.")
        # Different terminal segments — separate subjects within the stream
        assert SUBJECT_GROUND_LINK_DECISION_SNAPSHOT != SUBJECT_LINK_STATE_SNAPSHOT
