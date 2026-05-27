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

from nodalarc.models.link_decisions import LinkDecisionSnapshot, UnscheduledPair
from ome.snapshot_builder import build_link_decision_snapshot
from ome.types import GroundVisibilityDecision


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
        applied_max_range_km=None,
        applied_field_of_regard_deg=None,
        applied_max_tracking_rate_deg_s=None,
        applied_boresight_mode=None,
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
        assert isinstance(snap, LinkDecisionSnapshot)
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
            "applied_min_elevation_deg",
            "applied_max_range_km",
            "applied_field_of_regard_deg",
            "applied_max_tracking_rate_deg_s",
            "applied_boresight_mode",
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

    def test_unscheduled_pairs_passed_through(self):
        """The allocator already sorts unscheduled_pairs by pair. The
        builder passes them through unchanged."""
        pair = ("gs-a", "sat-2")
        unscheduled = UnscheduledPair(
            pair=pair,
            tenant_id="default",
            reference_body="earth",
            unscheduled_reason="sat_capacity",
            incumbent_pair=("gs-b", "sat-2"),
            capacity_constraint="sat-2.ground_terminals",
        )
        snap = build_link_decision_snapshot(
            decisions={},
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
        parsed = LinkDecisionSnapshot.model_validate_json(payload)
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
