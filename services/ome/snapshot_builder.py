# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME authoritative link-state snapshot builder.

Snapshot construction is serialization of already-computed OME state. It is
kept separate from compute_step so propagation, allocation, and event
transition logic can be tested without also exercising the LinkStateSnapshot
wire contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.geo import compute_latency_ms, compute_range_km
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)

from ome.ground_allocator import MbbTeardownState
from ome.propagation_engine import PropagatedState


def build_link_state_snapshot(
    isl_state: dict[tuple[str, str], tuple[bool, bool]],
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]],
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: Mapping[tuple[str, str], float],
    sim_time: datetime,
    seq: int,
    interval_s: float,
    propagated_states: Mapping[str, PropagatedState] | None = None,
    fixed_positions: Mapping[str, tuple[EcefVec3, GeoPosition]] | None = None,
    epoch_id: int = 0,
    current_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    mbb_pending_teardowns: MbbTeardownState | None = None,
    mbb_overlap_ticks: int = 3,
    current_step: int = 0,
) -> LinkStateSnapshot:
    """Build a LinkStateSnapshot from OME internal state.

    Active links require same-tick propagated ECEF state so range and
    one-way latency are OME-authoritative. Missing state for an active link is
    fatal here; publishing an active link without geometry would force
    downstream components to decide whether to invent or reject physics.
    """
    ecef: dict[str, EcefVec3] = {}
    if propagated_states:
        for node_id, state in propagated_states.items():
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

    links: list[LinkState] = []

    for pair, (visible, scheduled) in isl_state.items():
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
                sim_time=sim_time,
            )
        )

    assoc = current_associations or {}
    td_state = mbb_pending_teardowns or {}
    for pair, state_tuple in gs_state.items():
        visible = state_tuple[0]
        scheduled = state_tuple[1]
        sched_state = state_tuple[2] if len(state_tuple) > 2 else "active"
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
            start_tick, successor = td_state[pair]
            td_remaining = max(0, mbb_overlap_ticks - (current_step - start_tick))
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
            )
        )

    return LinkStateSnapshot(
        sim_time=sim_time,
        snapshot_seq=seq,
        links=tuple(links),
        interval_s=interval_s,
        epoch_id=epoch_id,
    )
