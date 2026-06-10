# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler desired-state construction helpers.

This module is the Scheduler-side OME authority boundary. It preserves
OME-provided range and one-way latency, derives configured interfaces and
bandwidth, and fails loudly when required authority is missing.
"""

from __future__ import annotations

from datetime import datetime

from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import AdminState, CarrierState, LinkState


class ActiveLinkInfo:
    """Mutable internal state for a desired or active link.

    TODO(trust-gap-closure#10): Make this a frozen dataclass. Mutability
    exists only because latency updates on active links currently assign
    latency_ms in place. Replace with dataclasses.replace() on a frozen
    type — the mutation pattern is self._actual_links[pair] = replace(info,
    latency_ms=new). This eliminates the mutable-value-in-frozen-intent
    contract gap where DispatchIntent is frozen but its dict values are not.
    """

    __slots__ = (
        "interface_a",
        "interface_b",
        "latency_ms",
        "netem_one_way_ms",
        "bandwidth_mbps",
        "link_type",
        "range_km",
        "authority_sim_time",
        "authority_source",
        "authority_sequence",
    )

    def __init__(
        self,
        interface_a: str,
        interface_b: str,
        latency_ms: float,
        bandwidth_mbps: float,
        *,
        link_type: str,
        range_km: float | None = None,
        authority_sim_time: datetime | None = None,
        authority_source: str | None = None,
        authority_sequence: int | None = None,
        netem_one_way_ms: float | None = None,
    ) -> None:
        self.interface_a = interface_a
        self.interface_b = interface_b
        self.latency_ms = latency_ms
        # The netem delay actually COMMANDED to the Node Agent for this link
        # (orbital latency minus the substrate compensation measured at
        # dispatch time). Set by the command builders when a LinkUp or
        # SetLatency is sent; kernel proofs assert against this value, never
        # against a live recomputation - compensation inputs drift between
        # dispatch and proof, and that drift is not kernel divergence.
        self.netem_one_way_ms = netem_one_way_ms
        self.bandwidth_mbps = bandwidth_mbps
        self.link_type = link_type
        self.range_km = range_km
        self.authority_sim_time = authority_sim_time
        self.authority_source = authority_source
        self.authority_sequence = authority_sequence


def require_ome_geometry(
    pair: tuple[str, str],
    *,
    range_km: float | None,
    latency_ms: float | None,
    source: str,
) -> tuple[float, float]:
    """Require OME-authoritative range and one-way latency.

    The Scheduler is not a physics authority. Missing range/latency is a
    corrupt control-plane input, not a condition to paper over with a fallback.
    Raising here stops dispatch before the emulator can apply made-up physics.
    """
    if range_km is None:
        raise ValueError(f"{source} for {pair} is missing OME-authoritative range_km")
    if latency_ms is None:
        raise ValueError(f"{source} for {pair} is missing OME-authoritative latency_ms")
    if range_km < 0:
        raise ValueError(f"{source} for {pair} has negative range_km={range_km}")
    if latency_ms < 0:
        raise ValueError(f"{source} for {pair} has negative latency_ms={latency_ms}")
    return range_km, latency_ms


def _ground_interfaces(
    pair: tuple[str, str],
    *,
    ground_station_ids: frozenset[str],
    gs_terminal_index: int | None,
    sat_terminal_index: int | None,
) -> tuple[str, str]:
    if gs_terminal_index is None or sat_terminal_index is None:
        raise ValueError(
            "Scheduled ground links require OME-provided terminal indices; "
            f"got gs_terminal_index={gs_terminal_index!r}, "
            f"sat_terminal_index={sat_terminal_index!r}"
        )

    a_is_ground = pair[0] in ground_station_ids
    b_is_ground = pair[1] in ground_station_ids
    if a_is_ground == b_is_ground:
        role = "no ground endpoint" if not a_is_ground else "two ground endpoints"
        raise ValueError(
            f"Scheduled ground link {pair} has {role}; refusing to infer "
            "interface ownership without exactly one configured ground station"
        )

    gs_iface = f"term{gs_terminal_index}"
    sat_iface = f"gnd{sat_terminal_index}"
    if a_is_ground:
        return gs_iface, sat_iface
    return sat_iface, gs_iface


def _configured_bandwidth(
    pair: tuple[str, str],
    bandwidth_map: dict[tuple[str, str], float],
    *,
    source: str,
) -> float:
    bandwidth = bandwidth_map.get(pair)
    if bandwidth is None or bandwidth <= 0:
        raise ValueError(
            f"{source} for {pair} has no config-derived bandwidth; "
            "refusing to dispatch a link with unknown physical rate"
        )
    return bandwidth


def desired_link_from_visibility(
    vis: VisibilityEvent,
    *,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    ground_station_ids: frozenset[str],
) -> tuple[tuple[str, str], ActiveLinkInfo]:
    """Build one desired link from a scheduled visible OME event."""
    pair = (vis.node_a, vis.node_b)
    range_km, latency = require_ome_geometry(
        pair,
        range_km=vis.range_km,
        latency_ms=vis.latency_ms,
        source="VisibilityEvent",
    )

    if vis.link_type == "ground":
        ifaces = _ground_interfaces(
            pair,
            ground_station_ids=ground_station_ids,
            gs_terminal_index=vis.gs_terminal_index,
            sat_terminal_index=vis.sat_terminal_index,
        )
    else:
        ifaces = interface_map.get(pair)
        if ifaces is None:
            raise ValueError(
                f"VisibilityEvent for {pair} has no configured ISL interfaces; "
                "refusing to dispatch an unmapped link"
            )

    bandwidth = _configured_bandwidth(pair, bandwidth_map, source="VisibilityEvent")
    return pair, ActiveLinkInfo(
        interface_a=ifaces[0],
        interface_b=ifaces[1],
        latency_ms=latency,
        bandwidth_mbps=bandwidth,
        link_type=vis.link_type,
        range_km=range_km,
        authority_sim_time=vis.sim_time,
        authority_source="visibility_event",
    )


def desired_link_from_snapshot_link(
    link: LinkState,
    *,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    ground_station_ids: frozenset[str],
    snapshot_sim_time: datetime,
    snapshot_seq: int,
) -> tuple[tuple[str, str], ActiveLinkInfo] | None:
    """Build one desired link from an authoritative snapshot link entry."""
    if link.admin != AdminState.UP or link.carrier != CarrierState.UP:
        return None
    if link.sim_time != snapshot_sim_time:
        raise ValueError(
            "LinkStateSnapshot contains a link whose sim_time does not match "
            f"the snapshot authority time: link={link.sim_time.isoformat()} "
            f"snapshot={snapshot_sim_time.isoformat()}"
        )

    pair = (link.node_a, link.node_b)
    range_km, latency = require_ome_geometry(
        pair,
        range_km=link.range_km,
        latency_ms=link.latency_ms,
        source="LinkStateSnapshot",
    )

    if link.link_type == "ground":
        ifaces = _ground_interfaces(
            pair,
            ground_station_ids=ground_station_ids,
            gs_terminal_index=link.gs_terminal_index,
            sat_terminal_index=link.sat_terminal_index,
        )
    else:
        ifaces = interface_map.get(pair)
        if ifaces is None:
            raise ValueError(
                f"LinkStateSnapshot for {pair} has no configured ISL interfaces; "
                "refusing to dispatch an unmapped link"
            )

    bandwidth = _configured_bandwidth(pair, bandwidth_map, source="LinkStateSnapshot")
    return pair, ActiveLinkInfo(
        interface_a=ifaces[0],
        interface_b=ifaces[1],
        latency_ms=latency,
        bandwidth_mbps=bandwidth,
        link_type=link.link_type,
        range_km=range_km,
        authority_sim_time=snapshot_sim_time,
        authority_source="link_state_snapshot",
        authority_sequence=snapshot_seq,
    )
