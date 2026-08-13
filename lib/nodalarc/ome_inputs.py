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

from nodalarc.body_frames import BodyFrame, body_runtime_support_for
from nodalarc.ephemeris_runtime import (
    SkyfieldBspEphemeris,
    body_states_at,
    runtime_config_from_resolved,
    session_epoch_unix,
)
from nodalarc.link_metadata import LinkRuleMetadata
from nodalarc.models.addressing import NeighborAssignment
from nodalarc.models.ephemeris import EphemerisConfig
from nodalarc.models.events import EphemerisBodyFrame
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.resolved_session import (
    ResolvedAccessTerminalSelection,
    ResolvedBodyFacts,
    ResolvedNode,
    ResolvedSession,
    ResolvedTerminalBlock,
)
from nodalarc.models.session import GroundSchedulingConfig
from nodalarc.ome_runtime import (
    GroundStation,
    GroundStationFile,
    GroundTerminal,
    IslTerminal,
    SatelliteGroundTerminal,
    SatelliteNode,
    retarget_satellites,
)
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import orbital_period_for_body

GroundLinkModel = Literal["geometry_only", "terminal_physics"]
PropagatorId = Literal["two-body", "keplerian-circular", "j2-mean-elements", "sgp4-tle"]
SessionPropagatorId = PropagatorId | Literal["mixed"]


def _ome_propagator_id(value: str) -> PropagatorId:
    mapping: dict[str, PropagatorId] = {
        "two_body": "two-body",
        "j2_mean_elements": "j2-mean-elements",
        "sgp4_tle": "sgp4-tle",
    }
    return mapping[value]


@dataclass(frozen=True)
class ResolvedOmeInputs:
    """Session-constant OME inputs built from ``ResolvedSession``."""

    satellites: list[SatelliteNode]
    addressing: ResolvedAddressingView
    gs_file: GroundStationFile | None
    neighbors: frozenset[tuple[str, NeighborAssignment]]
    period: float
    propagator_id: SessionPropagatorId
    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]
    rule_map: dict[tuple[str, str], LinkRuleMetadata]
    ground_candidate_satellites_by_gs: dict[str, tuple[str, ...]]
    node_metadata: dict[str, dict[str, object]]
    ground_scheduling: GroundSchedulingConfig
    ground_link_model: GroundLinkModel
    active_bodies: frozenset[str]
    body_frames: dict[str, BodyFrame]
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


def build_ome_inputs_from_resolved(resolved: ResolvedSession) -> ResolvedOmeInputs:
    """Build OME runtime inputs from the resolved catalog session."""

    body_frames = _body_frames_from_resolved(resolved)
    selected_access = resolved.selected_access_terminals_by_node()
    satellites = [
        _satellite_from_resolved(node, selected_access.get(node.node_id, ()))
        for node in resolved.nodes
        if node.kind == "satellite"
    ]
    if not satellites:
        raise ValueError("OME requires at least one satellite node")

    ground_candidate_satellites_by_gs = resolved.ground_candidate_satellites_by_gs()
    access_ground_ids = frozenset(ground_candidate_satellites_by_gs)
    all_ground_nodes = [node for node in resolved.nodes if node.kind == "ground_station"]
    ground_nodes = [node for node in all_ground_nodes if node.node_id in access_ground_ids]
    gs_file = _ground_file_from_resolved(
        ground_nodes,
        resolved.effective_ground_min_elevation_by_gs(),
        selected_access,
    )
    addressing = ResolvedAddressingView(resolved)
    neighbors = _neighbors_from_resolved(resolved)
    propagator_id = _single_ome_propagator(resolved)
    # Anchor every working photograph to the session epoch at birth. Every
    # consumer of these inputs - live pacing, batch timeline, coverage
    # preview, builder preview - propagates dt from an epoch it supplies;
    # the elements must be valid there, not at each orbit's own epoch.
    # Identity when the two coincide, which keeps shipped sessions
    # bit-identical.
    retarget_satellites(
        satellites,
        session_propagator_id=propagator_id,
        anchor_epoch_unix=_session_epoch_unix(resolved),
        body_frames=body_frames,
    )
    period = max(
        orbital_period_for_body(
            sat.elements,
            _required_body_frame(body_frames, sat.central_body),
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
        ground_link_model=(
            resolved.simulation.ground_link_model
            if resolved.simulation is not None
            else "terminal_physics"
        ),
        active_bodies=active_bodies,
        body_frames=body_frames,
        body_ephemeris=_body_ephemeris_from_resolved(
            resolved,
            active_bodies=active_bodies,
            period_s=period,
        ),
    )


def _body_frame_from_resolved_facts(facts: ResolvedBodyFacts) -> BodyFrame:
    try:
        runtime_support = body_runtime_support_for(facts.body_id)
    except ValueError as exc:
        raise ValueError(
            f"body {facts.body_id!r} has primitive physical facts, but the runtime "
            "has no rotation/J2 support for that body"
        ) from exc
    return BodyFrame(
        name=facts.body_id,
        mean_radius_km=facts.mean_radius_km,
        equatorial_radius_km=facts.equatorial_radius_km,
        polar_radius_km=facts.polar_radius_km,
        rotation_rate_rad_s=runtime_support.rotation_rate_rad_s,
        gravitational_parameter_km3_s2=facts.gravitational_parameter_km3_s2,
        j2=runtime_support.j2,
    )


def _body_frames_from_resolved(resolved: ResolvedSession) -> dict[str, BodyFrame]:
    frames = {facts.body_id: _body_frame_from_resolved_facts(facts) for facts in resolved.bodies}
    active = _active_bodies(resolved)
    missing = sorted(active - set(frames))
    if missing:
        raise ValueError(
            "resolved session is missing body primitive facts for active body/bodies: "
            + ", ".join(missing)
        )
    return frames


def _required_body_frame(body_frames: dict[str, BodyFrame], body_id: str) -> BodyFrame:
    try:
        return body_frames[body_id]
    except KeyError as exc:
        raise ValueError(f"resolved runtime is missing body frame for {body_id!r}") from exc


def _active_bodies(resolved: ResolvedSession) -> frozenset[str]:
    active = frozenset(
        body
        for node in resolved.nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    )
    if not active:
        raise ValueError("resolved session contains no active body references")
    return active


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


#: A body ephemeris spans [epoch, epoch + period]. A satellite-less session has
#: no orbital period to size it; the frames are queried only at the epoch, so any
#: positive span that covers it works. One day is ample and cheap to load.
_SATELLITE_LESS_EPHEMERIS_SPAN_S = 86400.0


def resolved_body_frames_at_epoch(
    resolved: ResolvedSession, epoch_unix: float
) -> dict[str, EphemerisBodyFrame]:
    """The session's body frames at one epoch, WITHOUT any satellite input.

    ``build_ome_inputs_from_resolved`` refuses a satellite-less session, but a
    body's physical facts and its position at the epoch are satellite-
    independent. This lets the builder render a grammar-valid ground-only
    session (render scale anchors on a body frame) rather than wall it — the OME
    satellite precondition is a runtime-readiness gate, not a grammar rule."""
    physical = _body_frames_from_resolved(resolved)
    active = _active_bodies(resolved)
    body_ephemeris = _body_ephemeris_from_resolved(
        resolved,
        active_bodies=active,
        period_s=_SATELLITE_LESS_EPHEMERIS_SPAN_S,
    )
    body_states = body_states_at(body_ephemeris, set(active), epoch_unix)
    frames: dict[str, EphemerisBodyFrame] = {}
    for body_id, state in sorted(body_states.items()):
        frame = physical[body_id]
        frames[body_id] = EphemerisBodyFrame(
            body_id=body_id,
            mean_radius_km=frame.mean_radius_km,
            equatorial_radius_km=frame.equatorial_radius_km,
            polar_radius_km=frame.polar_radius_km,
            gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
            rotation_rate_rad_s=frame.rotation_rate_rad_s,
            j2=frame.j2,
            origin_x_km=state.position_km.x,
            origin_y_km=state.position_km.y,
            origin_z_km=state.position_km.z,
            vel_x_km_s=state.velocity_km_s.x,
            vel_y_km_s=state.velocity_km_s.y,
            vel_z_km_s=state.velocity_km_s.z,
            provider=state.provider,
            kernel_id=state.kernel_id,
            quality_tier=state.quality_tier,
            frame=state.frame,
        )
    return frames


def _session_epoch_unix(resolved: ResolvedSession) -> float:
    # Single epoch owner: nodalarc.ephemeris_runtime.session_epoch_unix.
    return session_epoch_unix(resolved.time)


def _runtime_ephemeris_config(resolved: ResolvedSession) -> EphemerisConfig:
    if resolved.ephemeris is None:
        raise ValueError("resolved session has no ephemeris manifest")
    # Single mapping owner: nodalarc.ephemeris_runtime.runtime_config_from_resolved.
    return runtime_config_from_resolved(resolved.ephemeris)


def _orbit_epoch_unix(raw: str, node_id: str) -> float:
    """The orbit's declared epoch as unix seconds; refuses naive timestamps."""
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"satellite {node_id!r} orbit epoch {raw!r} lacks an explicit UTC offset")
    return parsed.timestamp()


def _satellite_from_resolved(
    node: ResolvedNode,
    access_selections: tuple[ResolvedAccessTerminalSelection, ...],
) -> SatelliteNode:
    if node.orbit is None:
        raise ValueError(f"satellite {node.node_id!r} is missing resolved orbit facts")
    if node.central_body is None:
        raise ValueError(f"satellite {node.node_id!r} is missing resolved central_body")
    if node.orbit.propagator == "sgp4_tle" and (
        node.orbit.tle_line_1 is None
        or node.orbit.tle_line_2 is None
        or node.orbit.norad_id is None
    ):
        raise ValueError(f"satellite {node.node_id!r} is missing resolved TLE facts")
    isl_blocks = [
        block for block in node.terminal_inventory if block.endpoint_role in {"isl", "crosslink"}
    ]
    authored_elements = OrbitalElements(
        semi_major_axis_km=node.orbit.semi_major_axis_km,
        inclination_rad=math.radians(node.orbit.inclination_deg),
        raan_rad=math.radians(node.orbit.raan_deg),
        mean_anomaly_rad=math.radians(node.orbit.mean_anomaly_deg),
        eccentricity=node.orbit.eccentricity,
        argument_of_perigee_rad=math.radians(node.orbit.argument_of_perigee_deg),
    )
    return SatelliteNode(
        plane=node.plane or 0,
        slot=node.slot or 0,
        local_plane=node.plane or 0,
        local_slot=node.slot or 0,
        node_id=node.node_id,
        local_node_id=node.local_node_id,
        segment_id=node.segment_id,
        central_body=node.central_body,
        elements=authored_elements,
        authored_elements=authored_elements,
        authored_epoch_unix=_orbit_epoch_unix(node.orbit.epoch, node.node_id),
        isl_terminal_count=sum(block.count for block in isl_blocks),
        ground_terminal_count=sum(len(item.interface_indices) for item in access_selections),
        isl_terminals=tuple(_isl_terminal(block) for block in isl_blocks),
        ground_terminals=tuple(_satellite_ground_terminal(item) for item in access_selections),
        tle_line_1=node.orbit.tle_line_1,
        tle_line_2=node.orbit.tle_line_2,
        norad_id=node.orbit.norad_id,
        propagator_id=_ome_propagator_id(node.orbit.propagator),
    )


def _ground_file_from_resolved(
    nodes: list[ResolvedNode],
    min_elevation_by_gs: dict[str, float],
    selected_access: dict[str, tuple[ResolvedAccessTerminalSelection, ...]],
) -> GroundStationFile | None:
    stations: list[GroundStation] = []
    for node in nodes:
        if node.surface_position is None:
            raise ValueError(f"ground node {node.node_id!r} is missing surface position")
        if node.ground_scheduling is None:
            raise ValueError(f"ground node {node.node_id!r} is missing ground scheduling")
        access_selections = selected_access.get(node.node_id, ())
        if not access_selections:
            continue
        scheduling = _ground_scheduling_config(node.ground_scheduling)
        stations.append(
            GroundStation(
                name=node.node_id,
                lat_deg=node.surface_position.lat_deg,
                lon_deg=node.surface_position.lon_deg,
                alt_m=node.surface_position.alt_m,
                min_elevation_deg=min_elevation_by_gs[node.node_id],
                terminals=tuple(_ground_terminal(item) for item in access_selections),
                tenant_id=node.tenant_id,
                reference_body=_node_reference_body(node),
                service_priority=node.service_priority or 10,
                selection_policy=scheduling.selection_policy,
                handover_policy=scheduling.handover_policy,
                handover_mode=scheduling.handover_mode,
                mbb_overlap_ticks=scheduling.mbb_overlap_ticks,
                mbb_reserve=scheduling.mbb_reserve,
            )
        )
    if not stations:
        return None
    return GroundStationFile(stations=stations)


def _single_ome_propagator(resolved: ResolvedSession) -> SessionPropagatorId:
    propagators = {
        node.orbit.propagator
        for node in resolved.nodes
        if node.kind == "satellite" and node.orbit is not None
    }
    if len(propagators) > 1:
        return "mixed"
    return _ome_propagator_id(next(iter(propagators)))


def _neighbors_from_resolved(
    resolved: ResolvedSession,
) -> frozenset[tuple[str, NeighborAssignment]]:
    assignments: list[tuple[str, NeighborAssignment]] = []
    nodes_by_id = {node.node_id: node for node in resolved.nodes}
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        left = nodes_by_id[candidate.node_a]
        right = nodes_by_id[candidate.node_b]
        link_type = _isl_link_type(left, right)
        interface_a, interface_b = candidate.fixed_interfaces
        assignments.append(
            (
                candidate.node_a,
                NeighborAssignment(
                    interface=interface_a,
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
                    interface=interface_b,
                    peer_node_id=candidate.node_a,
                    link_type=link_type,
                    priority=candidate.priority,
                    bandwidth_mbps=candidate.bandwidth_mbps,
                ),
            )
        )
    return frozenset(assignments)


def _isl_link_type(left: ResolvedNode, right: ResolvedNode) -> str:
    if left.kind == "satellite" and right.kind == "satellite":
        if (
            left.segment_id == right.segment_id
            and left.plane is not None
            and right.plane is not None
            and left.plane == right.plane
        ):
            return "intra_plane_isl"
        return "cross_plane_isl"
    return "cross_plane_isl"


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
            "reference_body": _node_reference_body(node),
            "frame_id": node.frame_id,
        }
        for node in resolved.nodes
    }


def _node_reference_body(node: ResolvedNode) -> str:
    if node.reference_body is not None:
        return node.reference_body
    if node.central_body is not None:
        return node.central_body
    raise ValueError(f"resolved node {node.node_id!r} is missing reference/central body")


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


def _satellite_ground_terminal(
    selection: ResolvedAccessTerminalSelection,
) -> SatelliteGroundTerminal:
    block = selection.block
    return SatelliteGroundTerminal(
        type=block.medium,
        count=block.count,
        interface_indices=selection.interface_indices,
        bandwidth_mbps=_required(block.bandwidth_mbps, block, "bandwidth_mbps"),
        max_range_km=_required(block.max_range_km, block, "max_range_km"),
        field_of_regard_deg=_required(
            block.field_of_regard_deg,
            block,
            "field_of_regard_deg",
        ),
        max_tracking_rate_deg_s=_required(
            block.tracking_rate_deg_s,
            block,
            "tracking_rate_deg_s",
        ),
        boresight=block.boresight,
    )


def _ground_terminal(selection: ResolvedAccessTerminalSelection) -> GroundTerminal:
    block = selection.block
    return GroundTerminal(
        id=block.terminal_id,
        type=block.medium,
        count=block.count,
        interface_indices=selection.interface_indices,
        bandwidth_mbps=_required(block.bandwidth_mbps, block, "bandwidth_mbps"),
        tracking_capacity=block.tracking_capacity or 1,
        max_range_km=_required(block.max_range_km, block, "max_range_km"),
        field_of_regard_deg=_required(
            block.field_of_regard_deg,
            block,
            "field_of_regard_deg",
        ),
        max_tracking_rate_deg_s=_required(
            block.tracking_rate_deg_s,
            block,
            "tracking_rate_deg_s",
        ),
        boresight=block.boresight,
    )


def _required(value: float | None, block: ResolvedTerminalBlock, field: str) -> float:
    if value is None:
        raise ValueError(
            f"resolved terminal {block.owner_node_id}:{block.terminal_id} is missing {field}"
        )
    return float(value)


# Effective ground elevation masks are resolved-session truth
# (ResolvedSession.effective_ground_min_elevation_by_gs) — OME enforcement and
# VS-API display read the same derivation, including rule-endpoint masks.


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
