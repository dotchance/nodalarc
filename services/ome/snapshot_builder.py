# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME authoritative link-state snapshot builder.

Snapshot construction is serialization of already-computed OME state. It is
kept separate from compute_step so propagation, allocation, and event
transition logic can be tested without also exercising the LinkStateSnapshot
wire contract.
"""

from __future__ import annotations

from datetime import datetime

from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.geo import compute_latency_ms, compute_range_km, geodetic_to_ecef
from nodalarc.models.events import NodePosition
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)

from ome.ground_allocator import MbbTeardownState


def build_link_state_snapshot(
    isl_state: dict[tuple[str, str], tuple[bool, bool]],
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]],
    interface_map: dict[tuple[str, str], tuple[str, str]],
    sim_time: datetime,
    seq: int,
    interval_s: float,
    positions: dict[str, NodePosition] | None = None,
    epoch_id: int = 0,
    current_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    mbb_pending_teardowns: MbbTeardownState | None = None,
    mbb_overlap_ticks: int = 3,
    current_step: int = 0,
) -> LinkStateSnapshot:
    """Build a LinkStateSnapshot from OME internal state.

    Active links require same-tick positions so range and one-way latency are
    OME-authoritative. Missing positions for an otherwise active link leave
    range/latency absent; downstream validators fail loudly rather than
    inventing geometry.
    """
    ecef: dict[str, EcefVec3] = {}
    if positions:
        for node_id, pos in positions.items():
            ecef[node_id] = geodetic_to_ecef(GeoPosition(pos.lat_deg, pos.lon_deg, pos.alt_km))

    def _link_range_latency(node_a: str, node_b: str) -> tuple[float, float] | None:
        pa, pb = ecef.get(node_a), ecef.get(node_b)
        if pa is None or pb is None:
            return None
        range_km = compute_range_km(pa, pb)
        return range_km, compute_latency_ms(range_km)

    links: list[LinkState] = []

    for pair, (visible, scheduled) in isl_state.items():
        ifaces = interface_map.get(pair)
        if not ifaces:
            continue
        carrier = CarrierState.UP if visible and scheduled else CarrierState.DOWN
        range_latency = (
            _link_range_latency(pair[0], pair[1]) if carrier == CarrierState.UP else None
        )
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
                bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
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
            _link_range_latency(pair[0], pair[1]) if carrier == CarrierState.UP else None
        )
        gs_ti, sat_ti = assoc.get(pair, (0, 0))
        td_remaining = None
        successor = None
        if pair in td_state:
            start_tick, successor = td_state[pair]
            td_remaining = max(0, mbb_overlap_ticks - (current_step - start_tick))
        links.append(
            LinkState(
                node_a=pair[0],
                node_b=pair[1],
                interface_a=f"term{gs_ti}",
                interface_b=f"gnd{sat_ti}",
                admin=AdminState.UP,
                carrier=carrier,
                routing=RoutingState.UNKNOWN,
                range_km=range_latency[0] if range_latency else None,
                latency_ms=range_latency[1] if range_latency else None,
                bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
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
