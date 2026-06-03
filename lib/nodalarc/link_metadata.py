# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Authoritative link interface and bandwidth metadata.

OME snapshots and Scheduler actuation must agree on interface names and
emulated bandwidth. This module resolves those values from constellation and
ground-terminal models once, instead of letting services invent defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.constellation_loader import (
    SatelliteNode,
    ground_link_bandwidth_mbps,
    isl_link_bandwidth_mbps,
    satellite_node_id,
)
from nodalarc.ground_terminals import ground_terminal_type, station_ground_terminal_type
from nodalarc.link_rule_candidates import DeclaredLinkCandidate
from nodalarc.models.addressing import (
    AddressingScheme,
    NeighborAssignment,
    assign_isl_neighbors,
    neighbors_by_node,
)
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import SessionConfig


@dataclass(frozen=True)
class LinkRuleMetadata:
    """Declaration metadata for one wireable link pair."""

    link_rule_id: str
    topology_mode: str
    endpoint_segments: tuple[str, str]


@dataclass(frozen=True)
class LinkMetadataMaps:
    """Interface and bandwidth metadata keyed by canonical node pair."""

    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]
    rule_map: dict[tuple[str, str], LinkRuleMetadata]


def build_link_metadata_maps(
    session: SessionConfig,
    addressing: AddressingScheme,
    *,
    constellation: ConstellationConfig,
    satellites: list[SatelliteNode] | tuple[SatelliteNode, ...],
    gs_file: GroundStationFile,
    neighbors: frozenset[tuple[str, NeighborAssignment]] | None = None,
    ground_candidate_satellites_by_gs: Mapping[str, tuple[str, ...]] | None = None,
    declared_candidates: tuple[DeclaredLinkCandidate, ...] = (),
) -> LinkMetadataMaps:
    """Build interface and bandwidth maps from physical terminal config.

    Bandwidth comes from terminal models. Missing bandwidth for any wireable
    link is a configuration error; callers must not substitute a nominal rate.
    """
    neighbor_assignments = (
        neighbors if neighbors is not None else assign_isl_neighbors(constellation, addressing)
    )
    by_node = neighbors_by_node(neighbor_assignments)

    sat_location: dict[str, tuple[int, int]] = {
        satellite_node_id(sat, addressing): (sat.plane, sat.slot) for sat in satellites
    }
    sats_by_id = {satellite_node_id(sat, addressing): sat for sat in satellites}

    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}
    rule_map: dict[tuple[str, str], LinkRuleMetadata] = {}
    node_segments: dict[str, str] = {}
    for sat in satellites:
        sat_id = satellite_node_id(sat, addressing)
        segment_id = getattr(sat, "segment_id", None)
        if segment_id is not None:
            node_segments[sat_id] = segment_id

    for candidate in declared_candidates:
        rule_map[candidate.pair] = LinkRuleMetadata(
            link_rule_id=candidate.rule_id,
            topology_mode=candidate.topology_mode,
            endpoint_segments=candidate.endpoint_segments,
        )

    for node_id, assignments in by_node.items():
        for assignment in assignments:
            pair = (min(node_id, assignment.peer_node_id), max(node_id, assignment.peer_node_id))
            existing = interface_map.get(pair, ("", ""))
            if node_id == pair[0]:
                interface_map[pair] = (assignment.interface, existing[1])
            else:
                interface_map[pair] = (existing[0], assignment.interface)
            if pair not in rule_map:
                segment_a = node_segments.get(pair[0])
                segment_b = node_segments.get(pair[1])
                if assignment.link_type.startswith("link_rule:"):
                    rule_id = assignment.link_type.removeprefix("link_rule:")
                    topology_mode = "declared"
                elif segment_a is not None and segment_a == segment_b:
                    rule_id = f"{segment_a}.internal_isl"
                    topology_mode = "structural"
                else:
                    rule_id = assignment.link_type
                    topology_mode = "structural"
                endpoint_segments = (
                    segment_a or "unknown",
                    segment_b or "unknown",
                )
                rule_map[pair] = LinkRuleMetadata(
                    link_rule_id=rule_id,
                    topology_mode=topology_mode,
                    endpoint_segments=endpoint_segments,
                )

    for pair, (iface_a, iface_b) in interface_map.items():
        if not iface_a or not iface_b:
            raise ValueError(
                "Interface map incomplete for "
                f"{pair}: iface_a={iface_a or '<empty>'}, iface_b={iface_b or '<empty>'}"
            )
        plane_a, slot_a = sat_location[pair[0]]
        plane_b, slot_b = sat_location[pair[1]]
        bandwidth_map[pair] = isl_link_bandwidth_mbps(
            constellation,
            plane_a,
            slot_a,
            iface_a,
            plane_b,
            slot_b,
            iface_b,
        )

    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        if ground_candidate_satellites_by_gs is None:
            raise ValueError(
                "Link metadata requires a declared ground-link candidate map "
                "when ground stations exist"
            )
        if gs_id not in ground_candidate_satellites_by_gs:
            raise ValueError(
                f"Ground station {gs_id!r} missing from declared ground-link candidate map"
            )
        candidate_sat_ids = tuple(ground_candidate_satellites_by_gs[gs_id])
        if not candidate_sat_ids:
            continue
        gs_type = station_ground_terminal_type(gs_file, station)
        for sat_id in candidate_sat_ids:
            if sat_id not in sats_by_id:
                raise ValueError(
                    f"Ground station {gs_id!r} declares unknown satellite candidate {sat_id!r}"
                )
            sat = sats_by_id[sat_id]
            sat_type = ground_terminal_type(sat.ground_terminals)
            if gs_type != sat_type:
                raise ValueError(
                    f"Ground terminal type mismatch for {gs_id}<->{sat_id}: "
                    f"ground station uses {gs_type!r}, satellite uses {sat_type!r}. "
                    "Mixed terminal types require an explicit compatibility model."
                )
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("term0", "gnd0")
            bandwidth_map[pair] = ground_link_bandwidth_mbps(
                constellation,
                gs_file,
                sat.plane,
                sat.slot,
                station.name,
            )

    return LinkMetadataMaps(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        rule_map=rule_map,
    )
