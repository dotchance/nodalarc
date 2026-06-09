# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME inputs derived from the resolved catalog runtime contract.

The OME algorithms already have mature physics/allocation inputs. This module
builds those inputs from ``ResolvedSession`` only, so OME startup does not read
or reconstruct retired session/constellation/ground-station configuration
shapes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nodalarc.body_frames import body_frame_for
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.ephemeris_runtime import SkyfieldBspEphemeris
from nodalarc.link_metadata import LinkRuleMetadata
from nodalarc.models.addressing import NeighborAssignment
from nodalarc.models.constellation import GroundTerminal, IslTerminal
from nodalarc.models.ephemeris import EphemerisConfig, EphemerisKernel
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
)
from nodalarc.models.resolved_session import ResolvedNode, ResolvedSession, ResolvedTerminalBlock
from nodalarc.models.session import GroundSchedulingConfig
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import orbital_period_for_body

GroundLinkModel = Literal["geometry_only", "terminal_physics"]
PropagatorId = Literal["keplerian-circular", "j2-mean-elements", "sgp4-tle"]


@dataclass(frozen=True)
class ResolvedOmeInputs:
    """Session-constant OME inputs built from ``ResolvedSession``."""

    satellites: list[SatelliteNode]
    addressing: ResolvedAddressingView
    gs_file: GroundStationFile | None
    neighbors: frozenset[tuple[str, NeighborAssignment]]
    period: float
    propagator_id: PropagatorId
    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]
    rule_map: dict[tuple[str, str], LinkRuleMetadata]
    ground_candidate_satellites_by_gs: dict[str, tuple[str, ...]]
    node_metadata: dict[str, dict[str, object]]
    ground_scheduling: GroundSchedulingConfig
    ground_link_model: GroundLinkModel
    active_bodies: frozenset[str]
    body_ephemeris: SkyfieldBspEphemeris | None


class ResolvedAddressingView:
    """Addressing methods OME needs, backed by resolved runtime node IDs."""

    def __init__(self, resolved: ResolvedSession) -> None:
        self._node_types = {
            node.node_id: ("satellite" if node.kind == "satellite" else "ground_station")
            for node in resolved.nodes
        }
        self._sat_by_plane_slot: dict[tuple[int, int], str] = {}
        self._ambiguous_plane_slots: set[tuple[int, int]] = set()
        for node in resolved.nodes:
            if node.kind == "satellite" and node.plane is not None and node.slot is not None:
                key = (node.plane, node.slot)
                if key in self._ambiguous_plane_slots:
                    continue
                if key in self._sat_by_plane_slot:
                    # Plane/slot are local metadata and can collide across
                    # segments. OME paths should use SatelliteNode.node_id; fail
                    # if a caller asks for an ambiguous global plane/slot ID.
                    self._ambiguous_plane_slots.add(key)
                    self._sat_by_plane_slot.pop(key)
                    continue
                self._sat_by_plane_slot[key] = node.node_id

    @property
    def has_type_registry(self) -> bool:
        return bool(self._node_types)

    def node_type(self, node_id: str) -> str:
        try:
            return self._node_types[node_id]
        except KeyError as exc:
            raise KeyError(f"node_id {node_id!r} not in resolved OME node registry") from exc

    def is_ground_segment(self, node_id: str) -> bool:
        return self.node_type(node_id) == "ground_station"

    def is_satellite(self, node_id: str) -> bool:
        return self.node_type(node_id) == "satellite"

    def sat_id(self, plane: int, slot: int) -> str:
        if (plane, slot) in self._ambiguous_plane_slots:
            raise KeyError(
                f"plane/slot ({plane}, {slot}) is not globally unique in this resolved session; "
                "use resolver-owned node_id"
            )
        try:
            return self._sat_by_plane_slot[(plane, slot)]
        except KeyError as exc:
            raise KeyError(
                f"plane/slot ({plane}, {slot}) is not globally unique in this resolved session; "
                "use resolver-owned node_id"
            ) from exc

    @staticmethod
    def gs_id(name: str) -> str:
        # Resolved ground station names passed to OME are already runtime node IDs.
        return name

    @staticmethod
    def isl_interfaces(count: int) -> list[str]:
        return [f"isl{i}" for i in range(count)]

    @staticmethod
    def term_interfaces(count: int) -> list[str]:
        return [f"term{i}" for i in range(count)]

    @staticmethod
    def gnd_interfaces(count: int) -> list[str]:
        return [f"gnd{i}" for i in range(count)]

    def ground_link_interfaces(
        self,
        pair: tuple[str, str],
        gs_terminal_index: int = 0,
        sat_terminal_index: int = 0,
    ) -> tuple[str, str]:
        if self.is_ground_segment(pair[0]):
            return (f"term{gs_terminal_index}", f"gnd{sat_terminal_index}")
        return (f"gnd{sat_terminal_index}", f"term{gs_terminal_index}")


def build_ome_inputs_from_resolved(resolved: ResolvedSession) -> ResolvedOmeInputs:
    """Build OME runtime inputs from the resolved catalog session."""

    satellites = [
        _satellite_from_resolved(node) for node in resolved.nodes if node.kind == "satellite"
    ]
    if not satellites:
        raise ValueError("OME requires at least one satellite node")

    ground_candidate_satellites_by_gs = resolved.ground_candidate_satellites_by_gs()
    access_ground_ids = frozenset(ground_candidate_satellites_by_gs)
    all_ground_nodes = [node for node in resolved.nodes if node.kind == "ground_station"]
    ground_nodes = [node for node in all_ground_nodes if node.node_id in access_ground_ids]
    gs_file = _ground_file_from_resolved(ground_nodes)
    addressing = ResolvedAddressingView(resolved)
    neighbors = _neighbors_from_resolved(resolved)
    propagator_id = _single_ome_propagator(resolved)
    period = max(
        orbital_period_for_body(
            sat.elements,
            body_frame_for(getattr(sat, "central_body", "earth")),
        )
        for sat in satellites
    )
    ground_scheduling = _allocator_wide_ground_scheduling(ground_nodes)
    active_bodies = _active_bodies(resolved)
    return ResolvedOmeInputs(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        period=period,
        propagator_id=propagator_id,
        interface_map=resolved.link_interface_map(),
        bandwidth_map=resolved.link_bandwidth_map(),
        rule_map=_rule_map_from_resolved(resolved),
        ground_candidate_satellites_by_gs=ground_candidate_satellites_by_gs,
        node_metadata=_node_metadata(resolved),
        ground_scheduling=ground_scheduling,
        ground_link_model="geometry_only",
        active_bodies=active_bodies,
        body_ephemeris=_body_ephemeris_from_resolved(
            resolved,
            active_bodies=active_bodies,
            period_s=period,
        ),
    )


def _active_bodies(resolved: ResolvedSession) -> frozenset[str]:
    return frozenset(
        body
        for node in resolved.nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    ) or frozenset({"earth"})


def _body_ephemeris_from_resolved(
    resolved: ResolvedSession,
    *,
    active_bodies: frozenset[str],
    period_s: float,
) -> SkyfieldBspEphemeris | None:
    required_bodies = set(active_bodies)
    if required_bodies <= {"earth"} and resolved.ephemeris is None:
        return None
    if resolved.ephemeris is None:
        raise ValueError(
            "OME requires a resolved ephemeris manifest for non-Earth body target(s): "
            + ", ".join(sorted(required_bodies - {"earth"}))
        )
    if resolved.time is None:
        raise ValueError("OME requires catalog session time to validate ephemeris coverage")
    epoch_unix = _session_epoch_unix(resolved)
    runtime_config = _runtime_ephemeris_config(resolved)
    return SkyfieldBspEphemeris.from_config(
        runtime_config,
        required_bodies=required_bodies,
        epoch_unix=epoch_unix,
        end_epoch_unix=epoch_unix + period_s,
    )


def _session_epoch_unix(resolved: ResolvedSession) -> float:
    if resolved.time is None:
        raise ValueError("catalog session time is required")
    raw = resolved.time.start_time
    value = raw.replace("Z", "+00:00")
    epoch = datetime.fromisoformat(value)
    if epoch.tzinfo is None:
        raise ValueError(f"catalog session start_time must include timezone: {raw!r}")
    return epoch.timestamp()


def _runtime_ephemeris_config(resolved: ResolvedSession) -> EphemerisConfig:
    ephemeris = resolved.ephemeris
    if ephemeris is None:
        raise ValueError("resolved session has no ephemeris manifest")
    kernels: list[EphemerisKernel] = []
    for kernel in ephemeris.kernels:
        if kernel.sha256 is None:
            raise ValueError(f"ephemeris kernel {kernel.id!r} requires sha256")
        if kernel.coverage_start is None or kernel.coverage_end is None:
            raise ValueError(
                f"ephemeris kernel {kernel.id!r} requires coverage_start and coverage_end"
            )
        kernels.append(
            EphemerisKernel(
                id=kernel.id,
                path=kernel.path,
                checksum=kernel.sha256,
                targets=list(kernel.targets),
                frame=kernel.frame,
                coverage_start=datetime.fromisoformat(kernel.coverage_start.replace("Z", "+00:00")),
                coverage_end=datetime.fromisoformat(kernel.coverage_end.replace("Z", "+00:00")),
            )
        )
    return EphemerisConfig(
        provider=ephemeris.provider,
        quality_tier=ephemeris.quality_tier,
        kernels=kernels,
    )


def _satellite_from_resolved(node: ResolvedNode) -> SatelliteNode:
    if node.orbit is None:
        raise ValueError(f"satellite {node.node_id!r} is missing resolved orbit facts")
    if node.orbit.eccentricity != 0.0:
        raise ValueError(
            f"OME circular propagators cannot run eccentric orbit {node.orbit.orbit_id!r} "
            f"for {node.node_id!r}; eccentric propagation must be implemented before this "
            "session can run"
        )
    isl_blocks = [
        block for block in node.terminal_inventory if block.endpoint_role in {"isl", "crosslink"}
    ]
    access_blocks = [block for block in node.terminal_inventory if block.endpoint_role == "access"]
    return SatelliteNode(
        plane=node.plane or 0,
        slot=node.slot or 0,
        local_plane=node.plane or 0,
        local_slot=node.slot or 0,
        node_id=node.node_id,
        local_node_id=node.local_node_id,
        segment_id=node.segment_id,
        central_body=node.central_body or "earth",
        elements=OrbitalElements(
            semi_major_axis_km=node.orbit.semi_major_axis_km,
            inclination_rad=math.radians(node.orbit.inclination_deg),
            raan_rad=math.radians(node.orbit.raan_deg),
            true_anomaly_rad=math.radians(node.orbit.mean_anomaly_deg),
        ),
        isl_terminal_count=sum(block.count for block in isl_blocks),
        ground_terminal_count=sum(block.count for block in access_blocks),
        isl_terminals=tuple(_isl_terminal(block) for block in isl_blocks),
        ground_terminals=tuple(_satellite_ground_terminal(block) for block in access_blocks),
    )


def _ground_file_from_resolved(nodes: list[ResolvedNode]) -> GroundStationFile | None:
    stations: list[GroundStationConfig] = []
    for node in nodes:
        if node.surface_position is None:
            raise ValueError(f"ground node {node.node_id!r} is missing surface position")
        if node.ground_scheduling is None:
            raise ValueError(f"ground node {node.node_id!r} is missing ground scheduling")
        access_blocks = [
            block for block in node.terminal_inventory if block.endpoint_role == "access"
        ]
        if not access_blocks:
            continue
        scheduling = _ground_scheduling_config(node.ground_scheduling)
        stations.append(
            GroundStationConfig(
                name=node.node_id,
                source_name=node.local_node_id,
                site_id=node.segment_id,
                site_node_id=node.local_node_id,
                display_name=node.local_node_id,
                lat_deg=node.surface_position.lat_deg,
                lon_deg=node.surface_position.lon_deg,
                alt_m=node.surface_position.alt_m,
                min_elevation_deg=_effective_ground_min_elevation(node),
                terminals=tuple(_ground_terminal(block) for block in access_blocks),
                tenant_id=node.tenant_id,
                reference_body=node.reference_body or "earth",
                service_priority=node.service_priority or 10,
                selection_policy=scheduling.selection_policy,
                handover_policy=scheduling.handover_policy,
                handover_mode=scheduling.handover_mode,
                mbb_overlap_ticks=scheduling.mbb_overlap_ticks,
                mbb_reserve=scheduling.mbb_reserve,
                tags=list(node.tags),
            )
        )
    if not stations:
        return None
    return GroundStationFile(stations=stations)


def _single_ome_propagator(resolved: ResolvedSession) -> PropagatorId:
    mapping: dict[str, PropagatorId] = {
        "two_body": "keplerian-circular",
        "j2_mean_elements": "j2-mean-elements",
        "sgp4_tle": "sgp4-tle",
    }
    propagators = {
        node.orbit.propagator
        for node in resolved.nodes
        if node.kind == "satellite" and node.orbit is not None
    }
    if len(propagators) != 1:
        raise ValueError(
            "OME currently requires one propagator across all satellite nodes; "
            f"got {sorted(propagators)}"
        )
    propagator = next(iter(propagators))
    if propagator == "sgp4_tle":
        raise ValueError(
            "OME catalog runtime does not yet materialize TLE records for sgp4_tle; "
            "refusing to run instead of synthesizing placeholder orbital inputs"
        )
    return mapping[propagator]


def _neighbors_from_resolved(
    resolved: ResolvedSession,
) -> frozenset[tuple[str, NeighborAssignment]]:
    assignments: list[tuple[str, NeighborAssignment]] = []
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        link_type = (
            "intra_plane_isl"
            if candidate.endpoint_segments[0] == candidate.endpoint_segments[1]
            else "cross_plane_isl"
        )
        assignments.append(
            (
                candidate.node_a,
                NeighborAssignment(
                    interface=candidate.interface_a,
                    peer_node_id=candidate.node_b,
                    link_type=link_type,
                    priority=candidate.priority,
                    bandwidth_mbps=candidate.bandwidth_mbps,
                ),
            )
        )
        assignments.append(
            (
                candidate.node_b,
                NeighborAssignment(
                    interface=candidate.interface_b,
                    peer_node_id=candidate.node_a,
                    link_type=link_type,
                    priority=candidate.priority,
                    bandwidth_mbps=candidate.bandwidth_mbps,
                ),
            )
        )
    return frozenset(assignments)


def _rule_map_from_resolved(resolved: ResolvedSession) -> dict[tuple[str, str], LinkRuleMetadata]:
    return {
        candidate.pair: LinkRuleMetadata(
            link_rule_id=candidate.rule_id,
            topology_mode=candidate.topology_mode,
            endpoint_segments=candidate.endpoint_segments,
        )
        for candidate in resolved.link_candidates
    }


def _node_metadata(resolved: ResolvedSession) -> dict[str, dict[str, object]]:
    return {
        node.node_id: {
            "segment_id": node.segment_id,
            "local_node_id": node.local_node_id,
            "namespace": node.namespace,
            "tags": tuple(node.tags),
            "reference_body": node.reference_body or node.central_body or "earth",
            "frame_id": node.frame_id,
        }
        for node in resolved.nodes
    }


def _isl_terminal(block: ResolvedTerminalBlock) -> IslTerminal:
    return IslTerminal(
        type=block.source_terminal_id or block.medium,
        count=block.count,
        role=None,
        max_range_km=_required(block.max_range_km, block, "max_range_km"),
        bandwidth_mbps=_required(block.bandwidth_mbps, block, "bandwidth_mbps"),
        max_tracking_rate_deg_s=_required(
            block.tracking_rate_deg_s,
            block,
            "tracking_rate_deg_s",
        ),
        field_of_regard_deg=_required(block.field_of_regard_deg, block, "field_of_regard_deg"),
    )


def _satellite_ground_terminal(block: ResolvedTerminalBlock) -> GroundTerminal:
    return GroundTerminal(
        type=block.medium,
        count=block.count,
        bandwidth_mbps=_required(block.bandwidth_mbps, block, "bandwidth_mbps"),
        max_range_km=block.max_range_km,
    )


def _ground_terminal(block: ResolvedTerminalBlock) -> GroundTerminalDef:
    return GroundTerminalDef(
        id=block.terminal_id,
        type=block.medium,
        count=block.count,
        bandwidth_mbps=_required(block.bandwidth_mbps, block, "bandwidth_mbps"),
        tracking_capacity=block.tracking_capacity or 1,
        max_range_km=block.max_range_km,
    )


def _required(value: float | None, block: ResolvedTerminalBlock, field: str) -> float:
    if value is None:
        raise ValueError(
            f"resolved terminal {block.owner_node_id}:{block.terminal_id} is missing {field}"
        )
    return float(value)


def _effective_ground_min_elevation(node: ResolvedNode) -> float:
    values = [
        block.min_elevation_deg
        for block in node.terminal_inventory
        if block.endpoint_role == "access" and block.min_elevation_deg is not None
    ]
    if not values:
        raise ValueError(f"ground node {node.node_id!r} has no access terminal elevation limit")
    return max(float(value) for value in values)


def _ground_scheduling_config(value) -> GroundSchedulingConfig:
    data: dict[str, object] = {}
    if value.selection_policy is not None:
        data["selection_policy"] = _selection_policy_spec(value.selection_policy)
    if value.handover_policy is not None:
        data["handover_policy"] = _handover_policy_spec(value.handover_policy)
    for field in (
        "ranking_order",
        "handover_mode",
        "mbb_overlap_ticks",
        "mbb_reserve",
        "mbb_preemption",
        "successor_abort_policy",
        "cross_tenant_displacement",
        "bbm_acquire_timeout_ticks",
    ):
        attr = getattr(value, field)
        if attr is not None:
            data[field] = attr
    return GroundSchedulingConfig.model_validate(data)


def _selection_policy_spec(policy) -> SelectionPolicySpec:
    data = policy.model_dump(mode="python", exclude_none=True)
    if "highest_elevation" in data:
        return SelectionPolicySpec(name="highest-elevation", params={})
    if "lowest_elevation" in data:
        return SelectionPolicySpec(name="lowest-elevation", params={})
    if "longest_remaining_pass" in data:
        return SelectionPolicySpec(
            name="longest-remaining-pass",
            params=data["longest_remaining_pass"],
        )
    raise ValueError(f"unsupported catalog selection policy shape: {data!r}")


def _handover_policy_spec(policy) -> HandoverPolicySpec:
    data = policy.model_dump(mode="python", exclude_none=True)
    if "hysteresis" in data:
        return HandoverPolicySpec(name="hysteresis", params=data["hysteresis"])
    if "hard_release" in data:
        return HandoverPolicySpec(name="none", params={})
    raise ValueError(f"unsupported catalog handover policy shape: {data!r}")


def _allocator_wide_ground_scheduling(nodes: list[ResolvedNode]) -> GroundSchedulingConfig:
    configs = [
        _ground_scheduling_config(node.ground_scheduling)
        for node in nodes
        if node.ground_scheduling is not None
    ]
    if not configs:
        return GroundSchedulingConfig()
    first = configs[0]
    allocator_fields = (
        "ranking_order",
        "mbb_preemption",
        "successor_abort_policy",
        "cross_tenant_displacement",
        "bbm_acquire_timeout_ticks",
    )
    mismatched = [
        field
        for field in allocator_fields
        if any(getattr(config, field) != getattr(first, field) for config in configs[1:])
    ]
    if mismatched:
        raise ValueError(
            "OME allocator-wide scheduling fields differ across ground nodes: "
            + ", ".join(mismatched)
        )
    return first
