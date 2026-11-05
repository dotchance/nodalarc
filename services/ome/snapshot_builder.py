# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME authoritative link-state snapshot builder.

Snapshot construction is serialization of already-computed OME state. It is
kept separate from compute_step so propagation, allocation, and event
transition logic can be tested without also exercising the LinkStateSnapshot
wire contract.

LEAF wire models here (per-link LinkState, per-pair decision wire)
are built with ``model_construct``: every value is OME-computed and
exactly typed at the call site, and re-validating the producer's own
output cost ~5.4 ms p95 per authority tick at flagship scale — a third
of the 16.7 ms 60x budget. The OUTER snapshots remain validated: their
model_validators are cross-field tripwires against producer
contradictions, and pydantic does not re-validate already-constructed
instance fields, so they stay cheap. The trade is explicit: leaf field
validation and coercion do not run on this path, so the round-trip
parity tests (validate(dump(built)) reproduces the dump byte-for-byte)
are the guard that construction stays type-exact. Touch a leaf field
type or add a leaf validator and those tests must move with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime

from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.link_metadata import LinkRuleMetadata
from nodalarc.models.link_decisions import (
    GroundAllocationEvent,
    GroundLinkDecisionSnapshot,
    GroundPolicyAudit,
    GroundVisibilityDecisionWire,
    UnscheduledPair,
)
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
    SchedulingState,
)

from ome.types import GroundVisibilityDecisionMap, MbbTeardownState

IslSnapshotState = Mapping[tuple[str, str], tuple[bool, bool]]
GroundSnapshotState = Mapping[tuple[str, str], tuple[bool, bool]]


@dataclass(frozen=True)
class LinkSnapshotSource:
    """Committed OME state used to serialize LinkStateSnapshot.

    This is not an event-replay cache. The Physicist builds it from the
    current tick's propagation, visibility, and allocation results. The
    Publisher consumes it directly when serializing authoritative snapshots.
    """

    isl_state: IslSnapshotState
    ground_state: GroundSnapshotState
    associations: Mapping[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    # Each link's committed same-instant range (km) from the tick's ISL feasibility and ground
    # decisions, measured in the common frame when its ends belong to two bodies.
    ranges_km: Mapping[tuple[str, str], float]
    # Each link's committed one-way light times (pair[0] to pair[1], pair[1] to pair[0]) from
    # the tick's signals.
    latencies_ms: Mapping[tuple[str, str], tuple[float, float]]
    # The opening sim time of each fixed long-delay link's open contact.
    contacts_opened: Mapping[tuple[str, str], datetime]


def build_link_decision_snapshot(
    *,
    decisions: GroundVisibilityDecisionMap,
    unscheduled_pairs: tuple[UnscheduledPair, ...],
    policy_audit: GroundPolicyAudit,
    allocation_events: tuple[GroundAllocationEvent, ...],
    sim_time: datetime,
    snapshot_seq: int,
    epoch_id: int,
) -> GroundLinkDecisionSnapshot:
    """Build the diagnostic companion to ``LinkStateSnapshot``.

    Converts the hot-path slotted ``GroundVisibilityDecision`` instances
    to Pydantic ``GroundVisibilityDecisionWire`` form for the NATS
    boundary. Decisions are sorted by pair for deterministic payloads —
    Direction 4 (multi-compute-node) requires that two Scheduler
    replicas receiving the same snapshot see the same ordering.

    The same ``snapshot_seq`` / ``sim_time`` as the companion
    ``LinkStateSnapshot`` so consumers can correlate the two by
    sequence and time.

    ``unscheduled_pairs`` is already a Pydantic tuple from the
    allocator (the allocator constructs UnscheduledPair instances
    directly). The order is preserved — the allocator already
    sorted by pair.
    """
    sorted_decisions = sorted(decisions.items(), key=lambda kv: kv[0])
    wire_decisions = tuple(
        GroundVisibilityDecisionWire.model_construct(**asdict(decision))
        for _pair, decision in sorted_decisions
    )
    # The OUTER snapshot stays validated: its model_validator is a
    # cross-field tripwire (unscheduled pairs must match visible
    # decisions) that catches producer contradictions — high value, and
    # cheap because pydantic does not re-validate the already-built
    # instance fields. Only the O(N) leaf constructions skip validation.
    return GroundLinkDecisionSnapshot(
        sim_time=sim_time,
        snapshot_seq=snapshot_seq,
        epoch_id=epoch_id,
        decisions=wire_decisions,
        unscheduled_pairs=unscheduled_pairs,
        policy_audit=policy_audit,
        allocation_events=allocation_events,
    )


def build_link_state_snapshot(
    source: LinkSnapshotSource,
    *,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    sim_time: datetime,
    seq: int,
    interval_s: float,
    fixed_positions: Mapping[str, tuple[EcefVec3, GeoPosition]] | None = None,
    epoch_id: int = 0,
    mbb_overlap_ticks_by_gs: Mapping[str, int] | None = None,
    current_step: int = 0,
    rule_map: Mapping[tuple[str, str], LinkRuleMetadata] | None = None,
) -> LinkStateSnapshot:
    """Build a LinkStateSnapshot from committed OME StepResult state.

    Every up link carries the range and the two one-way delays the tick committed. A missing
    value for an up link is fatal here; publishing an up link without geometry would force
    downstream components to decide whether to invent or reject physics.
    """

    def _link_range(pair: tuple[str, str]) -> float:
        try:
            return source.ranges_km[pair]
        except KeyError as exc:
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot: the tick committed no range for "
                f"up link {pair[0]}<->{pair[1]}"
            ) from exc

    def _committed_latencies(pair: tuple[str, str]) -> tuple[float, float]:
        try:
            return source.latencies_ms[pair]
        except KeyError as exc:
            raise ValueError(
                f"Cannot build authoritative LinkStateSnapshot for active link {pair}: "
                "the tick committed no light time for it"
            ) from exc

    def _link_rule_metadata(pair: tuple[str, str]) -> LinkRuleMetadata | None:
        if rule_map is None:
            return None
        return rule_map.get(pair)

    links: list[LinkState] = []

    for pair in source.contacts_opened:
        if source.isl_state.get(pair) != (True, True):
            raise ValueError(
                f"Cannot build LinkStateSnapshot: long-delay link {pair[0]}<->{pair[1]} has an "
                "open contact but its carrier is not up"
            )

    for pair, (visible, scheduled) in source.isl_state.items():
        if pair not in interface_map:
            raise ValueError(
                f"Cannot build LinkStateSnapshot for ISL link {pair}: "
                "missing configured interface metadata"
            )
        ifaces = interface_map[pair]
        carrier = CarrierState.UP if visible and scheduled else CarrierState.DOWN
        up = carrier == CarrierState.UP
        range_km = _link_range(pair) if up else None
        latencies = _committed_latencies(pair) if up else None
        rule_meta = _link_rule_metadata(pair)
        links.append(
            LinkState.model_construct(
                node_a=pair[0],
                node_b=pair[1],
                interface_a=ifaces[0],
                interface_b=ifaces[1],
                admin=AdminState.UP,
                carrier=carrier,
                routing=RoutingState.UNKNOWN,
                range_km=range_km,
                latency_a_to_b_ms=latencies[0] if latencies else None,
                latency_b_to_a_ms=latencies[1] if latencies else None,
                link_type="isl",
                # An ISL has no make-before-break teardown.
                scheduling_state="active",
                link_rule_id=rule_meta.link_rule_id if rule_meta is not None else None,
                topology_mode=rule_meta.topology_mode if rule_meta is not None else None,
                endpoint_segments=rule_meta.endpoint_segments if rule_meta is not None else None,
                contact_opened_at=source.contacts_opened.get(pair),
                sim_time=sim_time,
            )
        )

    assoc = source.associations
    td_state = source.pending_teardowns
    overlap_by_gs = dict(mbb_overlap_ticks_by_gs or {})
    ground_ids = set(overlap_by_gs)
    if fixed_positions:
        ground_ids.update(fixed_positions)

    def _ground_side(pair: tuple[str, str]) -> int:
        # Ground pairs are lexicographically sorted, so position never
        # identifies the ground endpoint — resolved ground identity does.
        in_ground = [index for index, node_id in enumerate(pair) if node_id in ground_ids]
        if len(in_ground) != 1:
            raise ValueError(
                f"Cannot build LinkStateSnapshot for ground link {pair}: expected exactly "
                f"one ground-station endpoint, found {len(in_ground)}"
            )
        return in_ground[0]

    def _overlap_ticks_for_ground_pair(pair: tuple[str, str]) -> int:
        for node_id in pair:
            if node_id in overlap_by_gs:
                return overlap_by_gs[node_id]
        raise ValueError(
            f"Cannot build LinkStateSnapshot for pending teardown {pair}: missing "
            "per-ground-station MBB overlap policy"
        )

    for pair, (visible, scheduled) in source.ground_state.items():
        sched_state: SchedulingState = "teardown" if pair in td_state else "active"
        if visible and scheduled:
            carrier = CarrierState.UP
        elif visible and not scheduled:
            carrier = CarrierState.LOWERLAYERDOWN
        else:
            carrier = CarrierState.DOWN
        up = carrier == CarrierState.UP
        range_km = _link_range(pair) if up else None
        latencies = _committed_latencies(pair) if up else None
        gs_ti: int | None
        sat_ti: int | None
        if carrier == CarrierState.UP:
            if pair not in assoc:
                raise ValueError(
                    "Cannot build authoritative LinkStateSnapshot for active "
                    f"ground link {pair}: missing OME terminal association"
                )
            gs_ti, sat_ti = assoc[pair]
        else:
            if pair in assoc:
                gs_ti, sat_ti = assoc[pair]
            else:
                gs_ti = sat_ti = None
        if gs_ti is not None:
            gs_index = _ground_side(pair)
            term_iface, gnd_iface = f"term{gs_ti}", f"gnd{sat_ti}"
            interface_a = term_iface if gs_index == 0 else gnd_iface
            interface_b = gnd_iface if gs_index == 0 else term_iface
        else:
            interface_a = ""
            interface_b = ""
        td_remaining = None
        successor = None
        if pair in td_state:
            teardown = td_state[pair]
            successor = teardown.successor_pair
            td_remaining = max(
                0, _overlap_ticks_for_ground_pair(pair) - (current_step - teardown.start_step)
            )
        rule_meta = _link_rule_metadata(pair)
        links.append(
            LinkState.model_construct(
                node_a=pair[0],
                node_b=pair[1],
                interface_a=interface_a,
                interface_b=interface_b,
                admin=AdminState.UP,
                carrier=carrier,
                routing=RoutingState.UNKNOWN,
                range_km=range_km,
                latency_a_to_b_ms=latencies[0] if latencies else None,
                latency_b_to_a_ms=latencies[1] if latencies else None,
                link_type="ground",
                gs_terminal_index=gs_ti,
                sat_terminal_index=sat_ti,
                scheduling_state=sched_state,
                teardown_remaining_ticks=td_remaining,
                successor_pair=successor,
                sim_time=sim_time,
                link_rule_id=rule_meta.link_rule_id if rule_meta is not None else None,
                topology_mode=rule_meta.topology_mode if rule_meta is not None else None,
                endpoint_segments=rule_meta.endpoint_segments if rule_meta is not None else None,
            )
        )

    # Outer snapshot validated for the same reason as the decision
    # snapshot: cross-field tripwires stay armed; leaf models carry the cost.
    return LinkStateSnapshot(
        sim_time=sim_time,
        snapshot_seq=seq,
        links=tuple(links),
        interval_s=interval_s,
        epoch_id=epoch_id,
    )


@dataclass(frozen=True)
class DeferredLinkStateSnapshot:
    """One authority tick's state-snapshot inputs, committed by the pacer.

    The pacing thread's job ends at committing typed step state; wire
    MATERIALIZATION (building the pydantic snapshot) and serialization
    both run on the publisher thread inside the pacer's sleep window —
    the dataclass-to-pydantic conversion of an authority tick measured
    4.3 ms at flagship scale, a quarter of the 16.7 ms 60x budget.
    Every captured field is a per-tick fresh object or a session-static
    map; nothing is mutated after the tick commits, so the deferred
    build is race-free. Pacemaker facts (sim_time, seq, epoch_id) are
    stamped here, on the pacing thread, at commit.
    """

    source: LinkSnapshotSource
    interface_map: dict[tuple[str, str], tuple[str, str]]
    rule_map: Mapping[tuple[str, str], LinkRuleMetadata] | None
    sim_time: datetime
    seq: int
    interval_s: float
    fixed_positions: Mapping[str, tuple[EcefVec3, GeoPosition]] | None
    epoch_id: int
    mbb_overlap_ticks_by_gs: Mapping[str, int] | None
    current_step: int

    def build(self) -> LinkStateSnapshot:
        return build_link_state_snapshot(
            self.source,
            interface_map=self.interface_map,
            rule_map=self.rule_map,
            sim_time=self.sim_time,
            seq=self.seq,
            interval_s=self.interval_s,
            fixed_positions=self.fixed_positions,
            epoch_id=self.epoch_id,
            mbb_overlap_ticks_by_gs=self.mbb_overlap_ticks_by_gs,
            current_step=self.current_step,
        )


@dataclass(frozen=True)
class DeferredLinkDecisionSnapshot:
    """The decision companion's inputs, committed by the pacer.

    Same contract as DeferredLinkStateSnapshot: per-tick fresh or
    session-static inputs, Pacemaker facts stamped at commit, built and
    serialized on the publisher thread.
    """

    decisions: GroundVisibilityDecisionMap
    unscheduled_pairs: tuple[UnscheduledPair, ...]
    policy_audit: GroundPolicyAudit
    allocation_events: tuple[GroundAllocationEvent, ...]
    sim_time: datetime
    snapshot_seq: int
    epoch_id: int

    def build(self) -> GroundLinkDecisionSnapshot:
        return build_link_decision_snapshot(
            decisions=self.decisions,
            unscheduled_pairs=self.unscheduled_pairs,
            policy_audit=self.policy_audit,
            allocation_events=self.allocation_events,
            sim_time=self.sim_time,
            snapshot_seq=self.snapshot_seq,
            epoch_id=self.epoch_id,
        )
