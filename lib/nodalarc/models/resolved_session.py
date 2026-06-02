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

from pydantic import BaseModel, ConfigDict

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
    count: int
    tracking_capacity: int | None = None
    max_range_km: float | None = None
    min_elevation_deg: float | None = None
    field_of_regard_deg: float | None = None
    tracking_rate_deg_s: float | None = None
    bandwidth_mbps: float | None = None
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


class ResolvedEndpoint(BaseModel):
    """A link-rule endpoint after selector resolution to concrete runtime node IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    terminal_role: TerminalRole
    terminal_medium: TerminalMediumLiteral | None = None
    node_ids: tuple[str, ...]  # resolved runtime node IDs; never empty (validated)


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
    sid_start: int
    sid_end: int


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

    def node_ids(self) -> tuple[str, ...]:
        """All runtime node IDs in resolution order."""
        return tuple(n.node_id for n in self.nodes)

    def node_by_id(self, node_id: str) -> ResolvedNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None
