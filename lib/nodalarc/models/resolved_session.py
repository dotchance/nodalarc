# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""ResolvedSession — the single authoritative runtime view (CDR-6).

``resolve_session`` turns either legacy or segment-form session YAML into one
``ResolvedSession``. Every runtime consumer (OME, Scheduler, Operator, VS-API,
MI, coverage preview) reads this model; none reconstruct a runtime view from raw
``SessionConfig`` + ``expand_constellation``, and none reload satellite-type or
ground-station source files — terminal truth is materialized here. Frozen across
the boundary. See ``specs/plans/multi-body-implementation-plan.md`` ("Resolver
Owns Runtime Identity").
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import FrameBodyName, SupportedSurfaceBody
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

    terminal_id: str
    owner_node_id: str
    endpoint_role: TerminalRole  # ground | isl | relay
    medium: TerminalMediumLiteral  # rf | optical
    count: int = Field(gt=0)
    tracking_capacity: int | None = Field(default=None, gt=0)
    max_range_km: float | None = Field(default=None, gt=0)
    min_elevation_deg: float | None = Field(default=None, ge=-90.0, le=90.0)
    field_of_regard_deg: float | None = Field(default=None, gt=0, le=360.0)
    tracking_rate_deg_s: float | None = Field(default=None, gt=0)
    bandwidth_mbps: float | None = Field(default=None, gt=0)
    # Provenance for audit/debug only (e.g. "satellite_type:starlink-v2-laser#isl[0]").
    source_ref: str


class ResolvedNode(BaseModel):
    """One runtime node with explicit identity, body/frame, terminals, and policy.

    Carries both ``local_node_id`` (source-segment ID before expansion) and
    ``node_id`` (runtime ID) so no consumer infers identity from string shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    local_node_id: str
    segment_id: str
    namespace: str | None
    kind: NodeKind
    frame_id: str
    central_body: FrameBodyName | None = None
    reference_body: SupportedSurfaceBody | None = None
    tags: tuple[str, ...] = ()
    satellite_type: str | None = None
    tenant_id: str = "default"
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
        return self


class ResolvedEndpoint(BaseModel):
    """A link-rule endpoint after selector resolution to concrete runtime node IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    terminal_role: TerminalRole
    terminal_medium: TerminalMediumLiteral | None = None
    # Resolved runtime node IDs; a selector that matched zero nodes is invalid.
    node_ids: tuple[str, ...] = Field(min_length=1)


class ResolvedLinkRule(BaseModel):
    """A link rule after selector resolution. Candidate generation starts here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: LinkKind
    enabled: bool
    endpoints: tuple[ResolvedEndpoint, ResolvedEndpoint]
    topology: LinkTopology
    constraints: LinkRuleConstraints | None = None
    protocol_boundary: ProtocolBoundary | None = None
    tags: tuple[str, ...] = ()


class SidBlock(BaseModel):
    """The disjoint segment-routing SID block allocated to one segment.

    Legacy/legacy-compatible sessions use one session-global block
    (``plane*100+slot+1``); segment_namespaced sessions get one disjoint block per
    segment so plane/slot are no longer globally meaningful.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
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
    origin: str
    session_path: str | None = None
    run_id: str | None = None


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
