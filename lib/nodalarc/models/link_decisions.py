# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Link-decision boundary types.

The OME computes a per-pair visibility *decision* every tick: physical
visibility plus the constraints applied to reach that decision, plus
why the pair is or is not scheduled. The decision is distinct from the
*actuated link state* carried on `LinkStateSnapshot` — `LinkStateSnapshot`
describes what the forwarding plane is doing right now; `GroundLinkDecisionSnapshot`
describes what the OME decided and why.

Two layers per the foundational trust plan:

- `GroundVisibilityDecisionWire` — Pydantic frozen, used at the NATS
  publish/parse boundary. Crosses component boundaries.
- The hot-path computational variant lives in `services/ome/types.py`
  as a slotted dataclass and is converted to the wire form at publish
  time. Inside the OME compute loop we never instantiate Pydantic
  models per pair — that would cost too much at constellation scale.

Direction 2 (multi-tenant from day one): every entity carries `tenant_id`.
Direction 3 (multi-body from day one): every decision carries the
`reference_body` it is anchored to and the `observer_frame` used to
compute the geometry. A future cislunar relay serving Earth and Luna
GSes will carry decisions for both bodies; the consumer reads the
field rather than assuming Earth.

These models are intentionally strict: every constructor argument is
required. There are no permissive defaults. A field that "could be
None" is one whose semantic meaning is "this constraint was not
applied" — never "we forgot to fill this in." If a field is unknown at
construction time, the producer is wrong; fix the producer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from nodalarc.models.terminal_physics import GroundBoresightMode

GroundVisibilityRejectReason = Literal[
    "ok",
    "los_blocked",
    "elevation_below_min",
    "range_exceeded",
    "field_of_regard",
    "tracking_exceeded",
]
"""Physical / geometric reason a ground-link pair fails visibility.

`ok` means the pair is geometrically visible. Other values describe
*why* the pair did not pass the per-terminal physics gate at the
ground-station boundary. These are independent of scheduling — a
visible pair may still be unscheduled (see `GroundUnscheduledReason`).

This is the GROUND-ONLY subset. ISL adds its own rejection reasons
(`polar_seam`, `terminal_type_mismatch`, `terminal_role_mismatch`) on
`VisibilityEvent`, but those values must never appear on a
`GroundVisibilityDecisionWire` — a ground decision rejected for an
ISL-only reason is a producer bug, and the type system refuses it."""


GroundUnscheduledReason = Literal[
    "gs_capacity",
    "sat_capacity",
    "hysteresis_hold",
    "incumbent_held",
    "bbm_no_spare",
    "replaced_by_successor",
]
"""Allocation reason a visible GROUND pair is not currently scheduled.

The ISL allocator's `isl_terminal_capacity` is intentionally absent
from this enum. `UnscheduledPair` lives in the ground-decision
snapshot (`GroundLinkDecisionSnapshot.unscheduled_pairs`), and a ground pair
stamped with an ISL-only reason is a producer bug. Future ISL
decision snapshots will carry their own typed reason.

A pair in active MBB teardown overlap is `(visible=True,
scheduled=True, scheduling_state="teardown")` and is NOT in
`unscheduled_pairs` — teardown is a scheduling state, not an
unscheduled-reason. The post-teardown released pair is
`replaced_by_successor`."""


ObserverFrame = Literal["body_local", "configured_inertial"]
"""Reference frame for `elevation_deg` / `azimuth_deg`.

`body_local` is the body-local-vertical frame anchored to the
observer's `reference_body` (ENU at Earth, MCMF-local-vertical at
Luna, etc.). `configured_inertial` is an explicitly configured
inertial vector relative to the body's reference frame — used for
Lagrange-point relays and other platforms with no natural local
vertical."""


class GroundVisibilityDecisionWire(BaseModel):
    """Per-pair ground visibility decision in wire form.

    Published as part of `GroundLinkDecisionSnapshot` on the
    `SUBJECT_GROUND_LINK_DECISION_SNAPSHOT` NATS subject
    (``nodalarc.links.<session>.ground_decisions``).

    Every field is required at construction. `applied_*` fields use
    `None` to mean "this constraint was not in effect for the decision"
    (e.g., a `geometry_only` session does not declare `max_range_km`,
    so the field is `None`); they never mean "we forgot to populate
    this." A `physical_v1` session must populate every applied
    constraint that the GS or sat terminal declares.

    Terminal profile fields explained:
    - `applied_gs_terminal_profile` and `applied_sat_terminal_profile`
      identify which *terminal definition / constraint profile* the
      visibility check evaluated against — NOT the kernel interface
      name (`term0`, `gnd0`) and NOT the terminal instance index.
      A satellite type may declare multiple `GroundTerminalDef`
      entries with different `max_range_km` / boresight values; the
      profile identifier names which definition's constraints applied
      to this decision. Use `None` only when the session is
      `geometry_only` and no terminal-level constraints were applied.
      The instance index (which physical terminal got the
      assignment) is carried separately on `VisibilityEvent` /
      `LinkState` as `gs_terminal_index` / `sat_terminal_index`.

    Invariant (enforced by the model validator below): if
    `reject_reason` names a terminal-bound constraint
    (`range_exceeded`, `field_of_regard`, `tracking_exceeded`), at
    least one side's profile MUST be identified — otherwise the
    rejection is unattributable to a specific terminal and cannot be
    audited.
    """

    model_config = ConfigDict(frozen=True)

    pair: tuple[str, str]
    tenant_id: str
    reference_body: str
    visible: bool
    range_km: float
    elevation_deg: float
    azimuth_deg: float | None
    observer_frame: ObserverFrame
    reject_reason: GroundVisibilityRejectReason
    applied_min_elevation_deg: float
    applied_max_range_km: float | None
    applied_field_of_regard_deg: float | None
    applied_max_tracking_rate_deg_s: float | None
    applied_boresight_mode: GroundBoresightMode | None
    applied_gs_terminal_profile: str | None
    applied_sat_terminal_profile: str | None

    @model_validator(mode="after")
    def _visible_iff_reject_ok(self) -> GroundVisibilityDecisionWire:
        """Foundational consistency: visible == (reject_reason == 'ok').

        A pair cannot simultaneously be visible and rejected. The two
        fields are not independent — they encode the same yes/no
        decision with the reason field carrying the *why* for the no
        case. Any other combination is an impossible state at the
        decision boundary; the producer is wrong.
        """
        if self.visible and self.reject_reason != "ok":
            raise ValueError(
                f"visible=True requires reject_reason='ok', got "
                f"{self.reject_reason!r}. A visible pair cannot also carry "
                "a rejection reason — the two fields must be consistent."
            )
        if not self.visible and self.reject_reason == "ok":
            raise ValueError(
                "visible=False requires a non-'ok' reject_reason — an "
                "invisible pair must carry the reason it failed visibility."
            )
        return self

    @model_validator(mode="after")
    def _terminal_constraint_must_name_profile(self) -> GroundVisibilityDecisionWire:
        """Terminal-bound rejections must name the terminal that rejected.

        If we say "this pair was rejected for range_exceeded," an
        auditor must be able to ask "which terminal's max range was
        exceeded?" The answer lives in the profile identifier. If
        neither side's profile is identified for a terminal-bound
        rejection, the producer is wrong — fail loud at construction.
        """
        if self.reject_reason in (
            "range_exceeded",
            "field_of_regard",
            "tracking_exceeded",
        ) and (
            self.applied_gs_terminal_profile is None and self.applied_sat_terminal_profile is None
        ):
            raise ValueError(
                f"reject_reason={self.reject_reason!r} requires at least one of "
                "applied_gs_terminal_profile / applied_sat_terminal_profile to be "
                "set — the rejection must be attributable to a specific terminal "
                "profile for audit."
            )
        return self


class UnscheduledPair(BaseModel):
    """A pair that was visible but not scheduled, with the reason.

    Carried in `GroundLinkDecisionSnapshot.unscheduled_pairs`. A pair appears
    here when `GroundVisibilityDecisionWire.visible=True` but the
    allocator did not schedule it.

    Teardown-overlap pairs do NOT appear here — they are scheduled
    (the old terminal is still active) and carry
    `scheduling_state="teardown"` on `VisibilityEvent` / `LinkState`.

    `incumbent_pair` is populated when this pair was rejected because
    a specific incumbent held the slot (`hysteresis_hold` or
    `incumbent_held`). `capacity_constraint` is populated when the
    reason names a specific resource that ran out (e.g., the sat
    ground-terminal id).
    """

    model_config = ConfigDict(frozen=True)

    pair: tuple[str, str]
    tenant_id: str
    reference_body: str
    unscheduled_reason: GroundUnscheduledReason
    incumbent_pair: tuple[str, str] | None
    capacity_constraint: str | None


class GroundLinkDecisionSnapshot(BaseModel):
    """Companion to `LinkStateSnapshot`: what the OME decided about
    GROUND links, and why.

    Ground-scoped. Carries decisions only for GS↔satellite pairs.
    ISL pair decisions are not snapshotted today; a separate
    `IslLinkDecisionSnapshot` type and subject will be added when
    ISL decision attribution lands. Consumers must not interpret an
    absent pair as "the OME ignored it" — it may simply be an ISL
    pair this snapshot does not cover.

    Published on `SUBJECT_GROUND_LINK_DECISION_SNAPSHOT` (same
    `sim_time` and `snapshot_seq` as the corresponding
    `LinkStateSnapshot`, for pairing).

    `LinkStateSnapshot` carries the actuated forwarding-plane state
    across both link types. `GroundLinkDecisionSnapshot` carries the
    OME's decision context for every GROUND pair the OME considered —
    visible-and-scheduled, visible-but-rejected, and
    visible-but-unscheduled.

    The two snapshots are correlatable by `sim_time` and
    `snapshot_seq`. Consumers asking "what is forwarding?" read the
    state snapshot; consumers asking "why isn't this ground link up?"
    read this decision snapshot.

    Internal consistency invariants (enforced at construction —
    Phase 1.1 boundary correctness):

    1. `decisions` has no duplicate pairs. Each (gs, sat) pair gets
       exactly one decision per snapshot.
    2. Every `UnscheduledPair` references a `pair` that is present in
       `decisions` with `visible=True`. An unscheduled pair by
       definition is visible-but-not-scheduled; pointing at an
       invisible pair or a pair not in the decision set is a
       producer bug.
    3. Every `UnscheduledPair`'s `tenant_id` and `reference_body`
       match the corresponding decision's. Divergence means the
       allocator and visibility engine disagree about which
       tenant/body owns the pair — a fatal contract break.
    4. `unscheduled_pairs` has no duplicate pairs.

    Producers that fail any of these are wrong. Phase 1.1 demands
    foundational types reject impossible states at construction.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    snapshot_seq: int
    epoch_id: int
    decisions: tuple[GroundVisibilityDecisionWire, ...]
    unscheduled_pairs: tuple[UnscheduledPair, ...]

    @model_validator(mode="after")
    def _decisions_have_unique_pairs(self) -> GroundLinkDecisionSnapshot:
        seen: set[tuple[str, str]] = set()
        for d in self.decisions:
            if d.pair in seen:
                raise ValueError(
                    f"GroundLinkDecisionSnapshot.decisions has duplicate pair {d.pair!r}. "
                    "Each pair must have exactly one decision per snapshot."
                )
            seen.add(d.pair)
        return self

    @model_validator(mode="after")
    def _unscheduled_pairs_consistent_with_decisions(self) -> GroundLinkDecisionSnapshot:
        decision_by_pair = {d.pair: d for d in self.decisions}
        seen: set[tuple[str, str]] = set()
        for u in self.unscheduled_pairs:
            if u.pair in seen:
                raise ValueError(
                    f"GroundLinkDecisionSnapshot.unscheduled_pairs has duplicate pair "
                    f"{u.pair!r}. Each unscheduled pair must appear at most once."
                )
            seen.add(u.pair)
            decision = decision_by_pair.get(u.pair)
            if decision is None:
                raise ValueError(
                    f"unscheduled_pair {u.pair!r} has no matching entry in "
                    "decisions. Every unscheduled pair must correspond to a "
                    "visibility decision in the same snapshot."
                )
            if not decision.visible:
                raise ValueError(
                    f"unscheduled_pair {u.pair!r} references a decision with "
                    "visible=False. An unscheduled pair is by definition "
                    "visible-but-not-scheduled — invisible pairs are simply "
                    "absent from allocation, not unscheduled."
                )
            if u.tenant_id != decision.tenant_id:
                raise ValueError(
                    f"unscheduled_pair {u.pair!r} tenant_id={u.tenant_id!r} "
                    f"disagrees with its decision tenant_id="
                    f"{decision.tenant_id!r}. Allocator and visibility engine "
                    "must agree on tenant ownership."
                )
            if u.reference_body != decision.reference_body:
                raise ValueError(
                    f"unscheduled_pair {u.pair!r} reference_body="
                    f"{u.reference_body!r} disagrees with its decision "
                    f"reference_body={decision.reference_body!r}. Allocator "
                    "and visibility engine must agree on the body anchor."
                )
        return self
