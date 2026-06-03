# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""ResolvedSession — the frozen runtime view produced by session resolution.

This module defines the authoritative object that the resolver will hand to OME,
Scheduler, Operator, VS-API, MI, and coverage preview. The model self-defends the
runtime truth it can validate locally: immutable config, concrete node identity,
materialized terminal inventory, disjoint SID blocks, and resolved link-rule
node sets. Consumer cutover happens in the resolver implementation; until that
lands, existing services still consume the resolver's internal ``SessionConfig``
runtime projection.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import FrameBodyName, SupportedSurfaceBody
from nodalarc.model_validation import NonEmptyReference
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import (
    LinkKind,
    LinkRuleConstraints,
    LinkTopology,
    ProtocolBoundary,
    TerminalRole,
)
from nodalarc.models.segments import SegmentClock
from nodalarc.models.session import (
    AddressingConfig,
    DispatchConfig,
    GroundSchedulingConfig,
    MiConfig,
    ObservabilityConfig,
    OrbitConfig,
    PlacementConfig,
    RoutingConfig,
    SchedulingConfig,
    SessionMeta,
    SimulationConfig,
    TerrestrialLinkConfig,
    TimeConfig,
    TrafficFlowConfig,
)

NodeKind = Literal["satellite", "ground_station", "relay"]
TerminalMediumLiteral = Literal["rf", "optical"]


class ResolvedTerminalBlock(BaseModel):
    """Materialized terminal truth for one terminal block on one node.

    Built from the resolved satellite_type (satellites) or station/ground-set
    terminal config (ground stations). Consumers read this; they do not reload
    the source file. ``tracking_capacity`` is a ground-station-terminal concept
    (simultaneous links per terminal) and is ``None`` for satellite terminals.
    Optional fields are ``None`` only when the source legitimately omits them; the
    resolver fails (never invents a default) when a value is required for a
    supported runtime feature.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    terminal_id: NonEmptyReference
    owner_node_id: NonEmptyReference
    endpoint_role: TerminalRole  # ground | isl | relay
    medium: TerminalMediumLiteral  # rf | optical
    source_terminal_id: NonEmptyReference | None = None
    count: int = Field(gt=0)
    tracking_capacity: int | None = Field(default=None, gt=0)
    max_range_km: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    min_elevation_deg: float | None = Field(default=None, ge=-90.0, le=90.0, allow_inf_nan=False)
    field_of_regard_deg: float | None = Field(default=None, gt=0, le=360.0, allow_inf_nan=False)
    tracking_rate_deg_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bandwidth_mbps: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Provenance for audit/debug only (e.g. "satellite_type:starlink-v2-laser#isl[0]").
    source_ref: NonEmptyReference


class ResolvedNode(BaseModel):
    """One runtime node with explicit identity, body/frame, terminals, and policy.

    Carries both ``local_node_id`` (source-segment ID before expansion) and
    ``node_id`` (runtime ID) so no consumer infers identity from string shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: NonEmptyReference
    local_node_id: NonEmptyReference
    segment_id: NonEmptyReference
    namespace: NonEmptyReference | None
    kind: NodeKind
    frame_id: NonEmptyReference
    central_body: FrameBodyName | None = None
    reference_body: SupportedSurfaceBody | None = None
    tags: tuple[NonEmptyReference, ...] = ()
    satellite_type: NonEmptyReference | None = None
    tenant_id: NonEmptyReference = "default"
    terminal_inventory: tuple[ResolvedTerminalBlock, ...] = ()
    # Complete resolved policy for ground stations; None for space nodes.
    ground_scheduling: GroundSchedulingConfig | None = None
    clock: SegmentClock = SegmentClock()

    @model_validator(mode="after")
    def _validate_terminals(self) -> ResolvedNode:
        seen: set[str] = set()
        for block in self.terminal_inventory:
            if block.terminal_id in seen:
                raise ValueError(
                    f"node {self.node_id!r} has duplicate terminal_id {block.terminal_id!r}"
                )
            seen.add(block.terminal_id)
            if block.owner_node_id != self.node_id:
                raise ValueError(
                    f"terminal {block.terminal_id!r} owner_node_id "
                    f"{block.owner_node_id!r} != node_id {self.node_id!r}"
                )
            if self.kind == "ground_station":
                if block.endpoint_role != "ground":
                    raise ValueError(
                        f"ground station {self.node_id!r} terminal {block.terminal_id!r} "
                        f"has non-ground endpoint_role {block.endpoint_role!r}"
                    )
                if block.tracking_capacity is None:
                    raise ValueError(
                        f"ground station {self.node_id!r} terminal {block.terminal_id!r} "
                        "requires tracking_capacity"
                    )
            elif block.tracking_capacity is not None:
                raise ValueError(
                    f"non-ground node {self.node_id!r} terminal {block.terminal_id!r} "
                    "must not set tracking_capacity"
                )
        if self.kind == "ground_station":
            if self.reference_body is None:
                raise ValueError(f"ground station {self.node_id!r} requires reference_body")
            if self.ground_scheduling is None:
                raise ValueError(f"ground station {self.node_id!r} requires ground_scheduling")
        elif self.ground_scheduling is not None:
            raise ValueError(f"non-ground node {self.node_id!r} must not set ground_scheduling")
        if self.kind == "satellite" and self.central_body is None:
            raise ValueError(f"satellite {self.node_id!r} requires central_body")
        return self


class ResolvedEndpoint(BaseModel):
    """A link-rule endpoint after selector resolution to concrete runtime node IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: NonEmptyReference
    terminal_role: TerminalRole
    terminal_medium: TerminalMediumLiteral | None = None
    terminal_id: NonEmptyReference | None = None
    # Resolved runtime node IDs; a selector that matched zero nodes is invalid.
    node_ids: tuple[NonEmptyReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_node_ids(self) -> ResolvedEndpoint:
        if len(set(self.node_ids)) != len(self.node_ids):
            dupes = sorted({n for n in self.node_ids if self.node_ids.count(n) > 1})
            raise ValueError(f"endpoint contains duplicate node_id(s): {dupes}")
        return self


class ResolvedLinkRule(BaseModel):
    """A link rule after selector resolution. Candidate generation starts here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: NonEmptyReference
    kind: LinkKind
    enabled: bool
    endpoints: tuple[ResolvedEndpoint, ResolvedEndpoint]
    topology: LinkTopology
    constraints: LinkRuleConstraints | None = None
    protocol_boundary: ProtocolBoundary | None = None
    tags: tuple[NonEmptyReference, ...] = ()

    @model_validator(mode="after")
    def _validate_endpoint_sets(self) -> ResolvedLinkRule:
        left = set(self.endpoints[0].node_ids)
        right = set(self.endpoints[1].node_ids)
        overlap = sorted(left & right)
        if overlap:
            raise ValueError(f"link rule {self.rule_id!r} has node(s) on both endpoints: {overlap}")
        return self


class SidBlock(BaseModel):
    """The disjoint segment-routing SID block allocated to one segment.

    Every supported session uses segment-namespaced identity, so each segment
    owns a disjoint SID block and plane/slot are no longer globally meaningful.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: NonEmptyReference
    sid_start: int = Field(ge=0)
    sid_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_range(self) -> SidBlock:
        if self.sid_end < self.sid_start:
            raise ValueError(
                f"SID block for segment {self.segment_id!r} is reversed: "
                f"sid_end {self.sid_end} < sid_start {self.sid_start}"
            )
        return self


class SourceContext(BaseModel):
    """Where the session came from — provenance the resolver records, not behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # e.g. "vs_api.deploy", "operator.reconcile", "wizard", "test".
    origin: NonEmptyReference
    session_path: NonEmptyReference | None = None
    run_id: NonEmptyReference | None = None


class ResolvedSession(BaseModel):
    """The single authoritative runtime view consumed by every service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_mode: IdentityMode
    session: SessionMeta
    nodes: tuple[ResolvedNode, ...]
    link_rules: tuple[ResolvedLinkRule, ...]
    sid_blocks: tuple[SidBlock, ...]
    # Reused session config surface (one orbit/routing/simulation/... contract).
    simulation: SimulationConfig
    orbit: OrbitConfig
    routing: RoutingConfig
    dispatch: DispatchConfig
    scheduling: SchedulingConfig
    addressing: AddressingConfig
    observability: ObservabilityConfig
    time: TimeConfig
    placement: PlacementConfig
    mi: MiConfig
    traffic_flows: tuple[TrafficFlowConfig, ...] = ()
    terrestrial_links: tuple[TerrestrialLinkConfig, ...] = ()
    source_context: SourceContext

    @model_validator(mode="after")
    def _validate_consistency(self) -> ResolvedSession:
        if self.identity_mode is not IdentityMode.SEGMENT_NAMESPACED:
            raise ValueError("ResolvedSession identity_mode must be segment_namespaced")

        ids = [n.node_id for n in self.nodes]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate runtime node_id(s): {dupes}")
        node_segment = {n.node_id: n.segment_id for n in self.nodes}
        known_segments = set(node_segment.values())

        seg_ids = [b.segment_id for b in self.sid_blocks]
        if len(set(seg_ids)) != len(seg_ids):
            raise ValueError("duplicate segment_id in sid_blocks")
        ghost_blocks = sorted(s for s in seg_ids if s not in known_segments)
        if ghost_blocks:
            raise ValueError(f"sid_blocks name segment(s) with no resolved nodes: {ghost_blocks}")
        # SID blocks must be disjoint — overlapping ranges defeat per-segment SID
        # allocation and silently corrupt forwarding.
        ordered = sorted(self.sid_blocks, key=lambda b: b.sid_start)
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if cur.sid_start <= prev.sid_end:
                raise ValueError(
                    f"SID blocks overlap: {prev.segment_id!r} "
                    f"[{prev.sid_start}..{prev.sid_end}] and {cur.segment_id!r} "
                    f"[{cur.sid_start}..{cur.sid_end}]"
                )

        rule_ids = [r.rule_id for r in self.link_rules]
        if len(set(rule_ids)) != len(rule_ids):
            dupes = sorted({r for r in rule_ids if rule_ids.count(r) > 1})
            raise ValueError(f"duplicate link rule id(s): {dupes}")
        flow_ids = [f.flow_id for f in self.traffic_flows]
        if len(set(flow_ids)) != len(flow_ids):
            dupes = sorted({f for f in flow_ids if flow_ids.count(f) > 1})
            raise ValueError(f"duplicate traffic flow id(s): {dupes}")
        link_pairs = [frozenset((t.station_a, t.station_b)) for t in self.terrestrial_links]
        if len(set(link_pairs)) != len(link_pairs):
            raise ValueError("duplicate terrestrial link station pair(s)")

        for rule in self.link_rules:
            for endpoint in rule.endpoints:
                missing = [nid for nid in endpoint.node_ids if nid not in node_segment]
                if missing:
                    raise ValueError(
                        f"link rule {rule.rule_id!r} endpoint references unknown "
                        f"node_id(s): {missing}"
                    )
                # An endpoint claims one segment; every node it lists must belong
                # to that segment (no cross-segment endpoint membership).
                foreign = sorted(
                    nid for nid in endpoint.node_ids if node_segment[nid] != endpoint.segment_id
                )
                if foreign:
                    raise ValueError(
                        f"link rule {rule.rule_id!r} endpoint segment "
                        f"{endpoint.segment_id!r} contains node(s) from another "
                        f"segment: {foreign}"
                    )
        return self

    def node_ids(self) -> tuple[str, ...]:
        """All runtime node IDs in resolution order."""
        return tuple(n.node_id for n in self.nodes)

    def node_by_id(self, node_id: str) -> ResolvedNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None
