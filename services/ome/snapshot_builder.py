# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME authoritative link-state snapshot builder.

Snapshot construction is serialization of already-computed OME state. It is
kept separate from compute_step so propagation, allocation, and event
transition logic can be tested without also exercising the LinkStateSnapshot
wire contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime

from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.geo import compute_latency_ms, compute_range_km
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
)

from ome.propagation_engine import PropagatedState
from ome.types import GroundVisibilityDecisionMap, MbbTeardownState

IslSnapshotState = Mapping[tuple[str, str], tuple[bool, bool]]
GroundSnapshotState = Mapping[tuple[str, str], tuple[bool, bool, str]]


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
    propagated_states: Mapping[str, PropagatedState]


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
        GroundVisibilityDecisionWire(**asdict(decision)) for _pair, decision in sorted_decisions
    )
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
    bandwidth_map: Mapping[tuple[str, str], float],
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

    Active links require same-tick propagated ECEF state so range and
    one-way latency are OME-authoritative. Missing state for an active link is
    fatal here; publishing an active link without geometry would force
    downstream components to decide whether to invent or reject physics.
    """
    ecef: dict[str, EcefVec3] = {}
    for node_id, state in source.propagated_states.items():
        ecef[node_id] = state.position_ecef_km
    if fixed_positions:
        for node_id, (position_ecef, _geo) in fixed_positions.items():
            ecef[node_id] = position_ecef

    def _link_range_latency(
        node_a: str,
        node_b: str,
        link_type: str,
    ) -> tuple[float, float]:
        pa, pb = ecef.get(node_a), ecef.get(node_b)
        if pa is None or pb is None:
            missing = ", ".join(node for node, pos in ((node_a, pa), (node_b, pb)) if pos is None)
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot for active "
                f"{link_type} link {node_a}<->{node_b}: missing same-tick ECEF state "
                f"for {missing}"
            )
        range_km = compute_range_km(pa, pb)
        return range_km, compute_latency_ms(range_km)

    def _link_bandwidth(pair: tuple[str, str], link_type: str) -> float:
        bandwidth = bandwidth_map.get(pair)
        if bandwidth is None or bandwidth <= 0:
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot for active "
                f"{link_type} link {pair}: missing config-derived bandwidth"
            )
        return bandwidth

    def _link_rule_metadata(pair: tuple[str, str]) -> LinkRuleMetadata | None:
        if rule_map is None:
            return None
        return rule_map.get(pair)

    links: list[LinkState] = []

    for pair, (visible, scheduled) in source.isl_state.items():
        if pair not in interface_map:
            raise ValueError(
                f"Cannot build LinkStateSnapshot for ISL link {pair}: "
                "missing configured interface metadata"
            )
        ifaces = interface_map[pair]
        carrier = CarrierState.UP if visible and scheduled else CarrierState.DOWN
        range_latency = (
            _link_range_latency(pair[0], pair[1], "isl") if carrier == CarrierState.UP else None
        )
        bandwidth_mbps = _link_bandwidth(pair, "isl") if carrier == CarrierState.UP else None
        rule_meta = _link_rule_metadata(pair)
        links.append(
            LinkState(
                node_a=pair[0],
                node_b=pair[1],
                interface_a=ifaces[0],
                interface_b=ifaces[1],
                admin=AdminState.UP,
                carrier=carrier,
                routing=RoutingState.UNKNOWN,
                range_km=range_latency[0] if range_latency else None,
                latency_ms=range_latency[1] if range_latency else None,
                bandwidth_mbps=bandwidth_mbps,
                link_type="isl",
                link_rule_id=rule_meta.link_rule_id if rule_meta is not None else None,
                topology_mode=rule_meta.topology_mode if rule_meta is not None else None,
                endpoint_segments=rule_meta.endpoint_segments if rule_meta is not None else None,
                sim_time=sim_time,
            )
        )

    assoc = source.associations
    td_state = source.pending_teardowns
    overlap_by_gs = dict(mbb_overlap_ticks_by_gs or {})

    def _overlap_ticks_for_ground_pair(pair: tuple[str, str]) -> int:
        for node_id in pair:
            if node_id in overlap_by_gs:
                return overlap_by_gs[node_id]
        raise ValueError(
            f"Cannot build LinkStateSnapshot for pending teardown {pair}: missing "
            "per-ground-station MBB overlap policy"
        )

    for pair, state_tuple in source.ground_state.items():
        visible = state_tuple[0]
        scheduled = state_tuple[1]
        sched_state = state_tuple[2]
        if visible and scheduled:
            carrier = CarrierState.UP
        elif visible and not scheduled:
            carrier = CarrierState.LOWERLAYERDOWN
        else:
            carrier = CarrierState.DOWN
        range_latency = (
            _link_range_latency(pair[0], pair[1], "ground") if carrier == CarrierState.UP else None
        )
        if carrier == CarrierState.UP:
            if pair not in assoc:
                raise ValueError(
                    "Cannot build authoritative LinkStateSnapshot for active "
                    f"ground link {pair}: missing OME terminal association"
                )
            gs_ti, sat_ti = assoc[pair]
            bandwidth_mbps = _link_bandwidth(pair, "ground")
        else:
            if pair in assoc:
                gs_ti, sat_ti = assoc[pair]
            else:
                gs_ti = sat_ti = None
            bandwidth_mbps = None
        interface_a = f"term{gs_ti}" if gs_ti is not None else ""
        interface_b = f"gnd{sat_ti}" if sat_ti is not None else ""
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
            LinkState(
                node_a=pair[0],
                node_b=pair[1],
                interface_a=interface_a,
                interface_b=interface_b,
                admin=AdminState.UP,
                carrier=carrier,
                routing=RoutingState.UNKNOWN,
                range_km=range_latency[0] if range_latency else None,
                latency_ms=range_latency[1] if range_latency else None,
                bandwidth_mbps=bandwidth_mbps,
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

    return LinkStateSnapshot(
        sim_time=sim_time,
        snapshot_seq=seq,
        links=tuple(links),
        interval_s=interval_s,
        epoch_id=epoch_id,
    )
