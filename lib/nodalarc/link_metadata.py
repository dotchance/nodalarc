# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Authoritative link interface and bandwidth metadata.

OME snapshots and Scheduler actuation must agree on interface names and
emulated bandwidth. This module resolves those values from constellation and
ground-terminal models once, instead of letting services invent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.constellation_loader import (
    SatelliteNode,
    ground_link_bandwidth_mbps,
    isl_link_bandwidth_mbps,
)
from nodalarc.ground_terminals import ground_terminal_type, station_ground_terminal_type
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import SessionConfig


@dataclass(frozen=True)
class LinkMetadataMaps:
    """Interface and bandwidth metadata keyed by canonical node pair."""

    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]


def build_link_metadata_maps(
    session: SessionConfig,
    addressing: AddressingScheme,
    *,
    constellation: ConstellationConfig,
    satellites: list[SatelliteNode] | tuple[SatelliteNode, ...],
    gs_file: GroundStationFile,
) -> LinkMetadataMaps:
    """Build interface and bandwidth maps from physical terminal config.

    Bandwidth comes from terminal models. Missing bandwidth for any wireable
    link is a configuration error; callers must not substitute a nominal rate.
    """
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    sat_location: dict[str, tuple[int, int]] = {
        addressing.sat_id(sat.plane, sat.slot): (sat.plane, sat.slot) for sat in satellites
    }
    sat_ground_types: dict[str, str] = {
        addressing.sat_id(sat.plane, sat.slot): ground_terminal_type(sat.ground_terminals)
        for sat in satellites
    }

    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}

    for node_id, assignments in by_node.items():
        for assignment in assignments:
            pair = (min(node_id, assignment.peer_node_id), max(node_id, assignment.peer_node_id))
            existing = interface_map.get(pair, ("", ""))
            if node_id == pair[0]:
                interface_map[pair] = (assignment.interface, existing[1])
            else:
                interface_map[pair] = (existing[0], assignment.interface)

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
        gs_type = station_ground_terminal_type(gs_file, station)
        for sat in satellites:
            sat_id = addressing.sat_id(sat.plane, sat.slot)
            sat_type = sat_ground_types[sat_id]
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

    return LinkMetadataMaps(interface_map=interface_map, bandwidth_map=bandwidth_map)
