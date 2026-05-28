# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for `build_link_decision_snapshot`.

Phase 1.3.b — the builder converts hot-path slotted
`GroundVisibilityDecision` instances to Pydantic
`GroundVisibilityDecisionWire` form for the NATS boundary, and packs
them together with the `UnscheduledPair` records into the wire-form
snapshot.

Pins:
- Conversion preserves every field of the hot-path decision.
- Deterministic ordering (pair sort) — Direction 4 requires that two
  Scheduler replicas receiving the same payload see the same order.
- Empty inputs construct cleanly (initial / seek snapshots).
- Round-trip through JSON survives.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nodalarc.models.link_decisions import (
    GroundLinkDecisionSnapshot,
    GroundVisibilityDecisionWire,
    UnscheduledPair,
)
from ome.snapshot_builder import build_link_decision_snapshot
from ome.types import GroundVisibilityDecision
from pydantic import ValidationError


def _hot_decision(
    pair: tuple[str, str],
    *,
    visible: bool = True,
    range_km: float = 1234.5,
    elevation_deg: float = 42.0,
) -> GroundVisibilityDecision:
    return GroundVisibilityDecision(
        pair=pair,
        tenant_id="default",
        reference_body="earth",
        visible=visible,
        range_km=range_km,
        elevation_deg=elevation_deg,
        azimuth_deg=180.0,
        observer_frame="body_local",
        reject_reason="ok" if visible else "elevation_below_min",
        applied_min_elevation_deg=25.0,
        rejecting_endpoint="none",
        applied_gs_max_range_km=None,
        applied_sat_max_range_km=None,
        applied_gs_field_of_regard_deg=None,
        applied_sat_field_of_regard_deg=None,
        applied_gs_max_tracking_rate_deg_s=None,
        applied_sat_max_tracking_rate_deg_s=None,
        applied_gs_boresight_mode=None,
        applied_sat_boresight_mode=None,
        applied_gs_terminal_profile=None,
        applied_sat_terminal_profile=None,
    )


SIM = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


class TestBuildLinkDecisionSnapshot:
    def test_empty_inputs_construct_valid_snapshot(self):
        """Initial-snap and seek-snap call with empty inputs. Must not
        raise; carries zero decisions and zero unscheduled pairs."""
        snap = build_link_decision_snapshot(
            decisions={},
            unscheduled_pairs=(),
            sim_time=SIM,
            snapshot_seq=1,
            epoch_id=0,
        )
        assert isinstance(snap, GroundLinkDecisionSnapshot)
        assert snap.decisions == ()
        assert snap.unscheduled_pairs == ()
        assert snap.sim_time == SIM
        assert snap.snapshot_seq == 1
        assert snap.epoch_id == 0

    def test_conversion_preserves_every_field(self):
        """Hot-path dataclass → wire Pydantic must round-trip every
        field. If the conversion silently drops anything, downstream
        consumers see a half-populated decision."""
        pair = ("gs-a", "sat-1")
        hot = _hot_decision(pair, visible=False, range_km=3000.0, elevation_deg=18.7)
        snap = build_link_decision_snapshot(
            decisions={pair: hot},
            unscheduled_pairs=(),
            sim_time=SIM,
            snapshot_seq=42,
            epoch_id=3,
        )
        assert len(snap.decisions) == 1
        wire = snap.decisions[0]
        # Every hot-path field must appear identically on the wire form.
        for field in (
            "pair",
            "tenant_id",
            "reference_body",
            "visible",
            "range_km",
            "elevation_deg",
            "azimuth_deg",
            "observer_frame",
            "reject_reason",
            "rejecting_endpoint",
            "applied_min_elevation_deg",
            "applied_gs_max_range_km",
            "applied_sat_max_range_km",
            "applied_gs_field_of_regard_deg",
            "applied_sat_field_of_regard_deg",
            "applied_gs_max_tracking_rate_deg_s",
            "applied_sat_max_tracking_rate_deg_s",
            "applied_gs_boresight_mode",
            "applied_sat_boresight_mode",
            "applied_gs_terminal_profile",
            "applied_sat_terminal_profile",
        ):
            assert getattr(hot, field) == getattr(wire, field), field

    def test_decisions_are_sorted_by_pair_deterministically(self):
        """Direction 4 — two Scheduler replicas receiving the same
        snapshot must see the same ordering. The builder sorts by
        pair."""
        a = ("gs-a", "sat-1")
        b = ("gs-b", "sat-1")
        c = ("gs-c", "sat-1")
        # Construct in non-sorted order.
        decisions = {b: _hot_decision(b), a: _hot_decision(a), c: _hot_decision(c)}
        snap = build_link_decision_snapshot(
            decisions=decisions,
            unscheduled_pairs=(),
            sim_time=SIM,
            snapshot_seq=10,
            epoch_id=0,
        )
        # Output order must be (a, b, c) regardless of input dict order.
        assert tuple(d.pair for d in snap.decisions) == (a, b, c)

    def test_unscheduled_pairs_must_have_matching_visible_decision(self):
        """Snapshot-level consistency: every unscheduled pair must
        reference a visible decision in the same snapshot. An empty
        decisions tuple with a non-empty unscheduled_pairs tuple is
        impossible (the unscheduled pair has nowhere to anchor)."""
        from pydantic import ValidationError

        pair = ("gs-a", "sat-2")
        unscheduled = UnscheduledPair(
            pair=pair,
            tenant_id="default",
            reference_body="earth",
            unscheduled_reason="sat_capacity",
            incumbent_pair=("gs-b", "sat-2"),
            capacity_constraint="sat-2.ground_terminals",
        )
        with pytest.raises(ValidationError, match="no matching entry in"):
            build_link_decision_snapshot(
                decisions={},
                unscheduled_pairs=(unscheduled,),
                sim_time=SIM,
                snapshot_seq=5,
                epoch_id=0,
            )

    def test_unscheduled_pairs_with_matching_decision_pass_through(self):
        """The normal path: an unscheduled pair anchored to a visible
        decision in the same snapshot. Builder passes through unchanged."""
        pair = ("gs-a", "sat-2")
        unscheduled = UnscheduledPair(
            pair=pair,
            tenant_id="default",
            reference_body="earth",
            unscheduled_reason="sat_capacity",
            incumbent_pair=("gs-b", "sat-2"),
            capacity_constraint="sat-2.ground_terminals",
        )
        decision = _hot_decision(pair, visible=True)
        snap = build_link_decision_snapshot(
            decisions={pair: decision},
            unscheduled_pairs=(unscheduled,),
            sim_time=SIM,
            snapshot_seq=5,
            epoch_id=0,
        )
        assert snap.unscheduled_pairs == (unscheduled,)

    def test_snapshot_round_trips_json(self):
        """Wire form must survive the NATS publish/parse boundary
        without losing structure."""
        pair = ("gs-a", "sat-1")
        snap = build_link_decision_snapshot(
            decisions={pair: _hot_decision(pair)},
            unscheduled_pairs=(),
            sim_time=SIM,
            snapshot_seq=99,
            epoch_id=2,
        )
        payload = snap.model_dump_json()
        parsed = GroundLinkDecisionSnapshot.model_validate_json(payload)
        assert parsed == snap

    def test_seq_and_epoch_are_carried(self):
        """The decision snapshot's seq and epoch must equal the
        companion LinkStateSnapshot's values — that's how consumers
        correlate them."""
        snap = build_link_decision_snapshot(
            decisions={},
            unscheduled_pairs=(),
            sim_time=SIM,
            snapshot_seq=7,
            epoch_id=4,
        )
        assert snap.snapshot_seq == 7
        assert snap.epoch_id == 4


# ---------------------------------------------------------------------------
# Snapshot-level consistency validators (Phase 1.1 boundary correctness)
# ---------------------------------------------------------------------------


def _wire_decision(
    pair: tuple[str, str],
    *,
    visible: bool = True,
    tenant_id: str = "default",
    reference_body: str = "earth",
) -> GroundVisibilityDecisionWire:
    return GroundVisibilityDecisionWire(
        pair=pair,
        tenant_id=tenant_id,
        reference_body=reference_body,
        visible=visible,
        range_km=1234.5,
        elevation_deg=42.0 if visible else 10.0,
        azimuth_deg=180.0,
        observer_frame="body_local",
        reject_reason="ok" if visible else "elevation_below_min",
        applied_min_elevation_deg=25.0,
        rejecting_endpoint="none",
        applied_gs_max_range_km=None,
        applied_sat_max_range_km=None,
        applied_gs_field_of_regard_deg=None,
        applied_sat_field_of_regard_deg=None,
        applied_gs_max_tracking_rate_deg_s=None,
        applied_sat_max_tracking_rate_deg_s=None,
        applied_gs_boresight_mode=None,
        applied_sat_boresight_mode=None,
        applied_gs_terminal_profile=None,
        applied_sat_terminal_profile=None,
    )


class TestLinkDecisionSnapshotConsistency:
    """Direct GroundLinkDecisionSnapshot construction must reject impossible
    states. Producers (snapshot builder) can pass through valid inputs,
    but the model is the last line of defense."""

    def test_duplicate_decision_pair_rejected(self) -> None:
        pair = ("gs-a", "sat-1")
        with pytest.raises(ValidationError, match="duplicate pair"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(_wire_decision(pair), _wire_decision(pair)),
                unscheduled_pairs=(),
            )

    def test_unscheduled_pair_without_matching_decision_rejected(self) -> None:
        with pytest.raises(ValidationError, match="no matching entry in"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(),
                unscheduled_pairs=(
                    UnscheduledPair(
                        pair=("gs-a", "sat-1"),
                        tenant_id="default",
                        reference_body="earth",
                        unscheduled_reason="sat_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                ),
            )

    def test_unscheduled_pair_pointing_at_invisible_decision_rejected(self) -> None:
        pair = ("gs-a", "sat-1")
        with pytest.raises(ValidationError, match="visible=False"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(_wire_decision(pair, visible=False),),
                unscheduled_pairs=(
                    UnscheduledPair(
                        pair=pair,
                        tenant_id="default",
                        reference_body="earth",
                        unscheduled_reason="sat_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                ),
            )

    def test_unscheduled_pair_tenant_mismatch_rejected(self) -> None:
        pair = ("gs-a", "sat-1")
        with pytest.raises(ValidationError, match="tenant_id"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(_wire_decision(pair, tenant_id="tenant-a"),),
                unscheduled_pairs=(
                    UnscheduledPair(
                        pair=pair,
                        tenant_id="tenant-b",
                        reference_body="earth",
                        unscheduled_reason="sat_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                ),
            )

    def test_unscheduled_pair_body_mismatch_rejected(self) -> None:
        pair = ("gs-a", "sat-1")
        with pytest.raises(ValidationError, match="reference_body"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(_wire_decision(pair, reference_body="earth"),),
                unscheduled_pairs=(
                    UnscheduledPair(
                        pair=pair,
                        tenant_id="default",
                        reference_body="luna",
                        unscheduled_reason="sat_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                ),
            )

    def test_duplicate_unscheduled_pair_rejected(self) -> None:
        pair = ("gs-a", "sat-1")
        with pytest.raises(ValidationError, match="duplicate pair"):
            GroundLinkDecisionSnapshot(
                sim_time=SIM,
                snapshot_seq=1,
                epoch_id=0,
                decisions=(_wire_decision(pair),),
                unscheduled_pairs=(
                    UnscheduledPair(
                        pair=pair,
                        tenant_id="default",
                        reference_body="earth",
                        unscheduled_reason="sat_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                    UnscheduledPair(
                        pair=pair,
                        tenant_id="default",
                        reference_body="earth",
                        unscheduled_reason="gs_capacity",
                        incumbent_pair=None,
                        capacity_constraint=None,
                    ),
                ),
            )

    def test_valid_snapshot_with_consistent_unscheduled_pair(self) -> None:
        pair = ("gs-a", "sat-1")
        snap = GroundLinkDecisionSnapshot(
            sim_time=SIM,
            snapshot_seq=1,
            epoch_id=0,
            decisions=(_wire_decision(pair, visible=True),),
            unscheduled_pairs=(
                UnscheduledPair(
                    pair=pair,
                    tenant_id="default",
                    reference_body="earth",
                    unscheduled_reason="sat_capacity",
                    incumbent_pair=None,
                    capacity_constraint=None,
                ),
            ),
        )
        assert len(snap.decisions) == 1
        assert len(snap.unscheduled_pairs) == 1
