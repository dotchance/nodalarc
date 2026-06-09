# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""ResolvedSession — the frozen runtime view produced by session resolution.

This module defines the authoritative object that the resolver will hand to OME,
Scheduler, Operator, VS-API, MI, and coverage preview. The model self-defends the
runtime truth it can validate locally: immutable config, concrete node identity,
materialized terminal inventory, disjoint SID blocks, and resolved link-rule
node sets.
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
    TerminalRole,
)
from nodalarc.models.segment_session import (
    Addressing,
    AreaAssignment,
    Dispatch,
    Routing,
    RoutingTimers,
    SessionMeta,
    Simulation,
    TimeConfig,
)
from nodalarc.models.segments import GroundScheduling, OriginatedPrefixes, SegmentClock

NodeKind = Literal["satellite", "ground_station", "relay"]
TerminalMediumLiteral = Literal["rf", "optical"]


class ResolvedOrbitFacts(BaseModel):
    """Runtime orbital facts for one resolved space node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orbit_id: NonEmptyReference
    central_body: FrameBodyName
    epoch: NonEmptyReference
    propagator: Literal["two_body", "j2_mean_elements", "sgp4_tle"]
    semi_major_axis_km: float = Field(gt=0, allow_inf_nan=False)
    eccentricity: float = Field(ge=0, lt=1, allow_inf_nan=False)
    inclination_deg: float = Field(allow_inf_nan=False)
    raan_deg: float = Field(allow_inf_nan=False)
    argument_of_perigee_deg: float = Field(allow_inf_nan=False)
    mean_anomaly_deg: float = Field(allow_inf_nan=False)


class ResolvedSurfacePosition(BaseModel):
    """Fixed body-surface position for one placed node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: SupportedSurfaceBody
    lat_deg: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    alt_m: float = Field(allow_inf_nan=False)


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
    endpoint_role: TerminalRole
    medium: TerminalMediumLiteral  # rf | optical
    source_terminal_id: NonEmptyReference | None = None
    link_role: NonEmptyReference | None = None
    count: int = Field(gt=0)
    tracking_capacity: int | None = Field(default=None, gt=0)
    max_range_km: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    min_elevation_deg: float | None = Field(default=None, ge=-90.0, le=90.0, allow_inf_nan=False)
    field_of_regard_deg: float | None = Field(default=None, gt=0, le=360.0, allow_inf_nan=False)
    tracking_rate_deg_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bandwidth_mbps: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Provenance for audit/debug only (e.g. "satellite_type:starlink-v2-laser#isl[0]").
    source_ref: NonEmptyReference


class ResolvedInterfaceAddress(BaseModel):
    """A numbered interface address set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ipv4: NonEmptyReference | None = None
    ipv6: NonEmptyReference | None = None

    @model_validator(mode="after")
    def _has_family(self) -> ResolvedInterfaceAddress:
        if self.ipv4 is None and self.ipv6 is None:
            raise ValueError("interface address requires ipv4 and/or ipv6")
        return self


class ResolvedNodeInterfaces(BaseModel):
    """Numbered interfaces authored by placement or allocated by the resolver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo0: ResolvedInterfaceAddress
    terr0: ResolvedInterfaceAddress | None = None


class ResolvedWanInterface(BaseModel):
    """Derived unnumbered WAN interface created from a terminal mount."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyReference
    owner_node_id: NonEmptyReference
    terminal_id: NonEmptyReference
    borrows: Literal["lo0"] = "lo0"


class ResolvedRoutingDomain(BaseModel):
    """One routing domain after selector resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain_id: NonEmptyReference
    protocol: Literal["isis", "ospf", "bgp", "static"]
    node_ids: tuple[NonEmptyReference, ...] = Field(min_length=1)
    capabilities: tuple[NonEmptyReference, ...] = ()
    area_assignment: AreaAssignment | None = None
    # Effective timer values — defaults applied at resolution, so every
    # consumer reads one populated truth and templates carry no fallbacks.
    timers: RoutingTimers = RoutingTimers()

    @model_validator(mode="after")
    def _unique_node_ids(self) -> ResolvedRoutingDomain:
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError(f"routing domain {self.domain_id!r} contains duplicate node ids")
        return self


class ResolvedEphemerisKernel(BaseModel):
    """One ephemeris kernel after target references resolve to body IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: NonEmptyReference
    path: NonEmptyReference
    sha256: NonEmptyReference | None = None
    targets: tuple[FrameBodyName, ...] = Field(min_length=1)
    frame: NonEmptyReference
    coverage_start: NonEmptyReference | None = None
    coverage_end: NonEmptyReference | None = None


class ResolvedEphemeris(BaseModel):
    """Resolved ephemeris manifest carried to runtime physics consumers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["skyfield_bsp", "spice_kernel_stack", "operator_supplied_spk"]
    quality_tier: NonEmptyReference
    kernels: tuple[ResolvedEphemerisKernel, ...] = Field(min_length=1)


class ResolvedBodyFacts(BaseModel):
    """Primitive-owned physical facts for one resolved body.

    Body primitives own these values. Runtime consumers must read them from the
    resolved session, not from hard-coded Earth/Luna tables.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    body_id: NonEmptyReference
    display_name: str
    gravitational_parameter_km3_s2: float = Field(gt=0, allow_inf_nan=False)
    mean_radius_km: float = Field(gt=0, allow_inf_nan=False)
    equatorial_radius_km: float = Field(gt=0, allow_inf_nan=False)
    polar_radius_km: float = Field(gt=0, allow_inf_nan=False)
    reference: str


class ResolvedLinkCandidate(BaseModel):
    """One declared static candidate pair with concrete runtime interfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: NonEmptyReference
    kind: LinkKind
    terminal_role: TerminalRole
    terminal_medium: TerminalMediumLiteral | None = None
    node_a: NonEmptyReference
    node_b: NonEmptyReference
    interface_a: NonEmptyReference
    interface_b: NonEmptyReference
    bandwidth_mbps: float = Field(gt=0, allow_inf_nan=False)
    topology_mode: NonEmptyReference
    priority: int = Field(ge=0)
    endpoint_segments: tuple[NonEmptyReference, NonEmptyReference]

    @model_validator(mode="after")
    def _pair_is_not_self(self) -> ResolvedLinkCandidate:
        if self.node_a == self.node_b:
            raise ValueError(f"link candidate {self.rule_id!r} has identical endpoints")
        return self

    @property
    def pair(self) -> tuple[str, str]:
        return (self.node_a, self.node_b)


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
    # Ground nodes: every placement group (ground segment) that placed this
    # node's site. A physical site exists once; placement groups are labels,
    # not namespaces, so one node may belong to several. segment_id holds the
    # first placing group for surfaces that need a single primary label.
    # Empty for space nodes (their segment IS their namespace).
    placement_groups: tuple[NonEmptyReference, ...] = ()
    kind: NodeKind
    frame_id: NonEmptyReference
    central_body: FrameBodyName | None = None
    reference_body: SupportedSurfaceBody | None = None
    tags: tuple[NonEmptyReference, ...] = ()
    satellite_type: NonEmptyReference | None = None
    tenant_id: NonEmptyReference = "default"
    terminal_inventory: tuple[ResolvedTerminalBlock, ...] = ()
    interfaces: ResolvedNodeInterfaces | None = None
    wan_interfaces: tuple[ResolvedWanInterface, ...] = ()
    orbit: ResolvedOrbitFacts | None = None
    surface_position: ResolvedSurfacePosition | None = None
    originated_prefixes: OriginatedPrefixes | None = None
    forwarding: Literal["routed", "host", "bridge", "control_only"] | None = None
    service_priority: int | None = Field(default=None, gt=0)
    plane: int | None = Field(default=None, ge=0)
    slot: int | None = Field(default=None, ge=0)
    # Complete resolved policy for ground stations; None for space nodes.
    ground_scheduling: GroundScheduling | None = None
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
            if block.tracking_capacity is None:
                raise ValueError(
                    f"node {self.node_id!r} terminal {block.terminal_id!r} "
                    "requires tracking_capacity"
                )
        if self.kind == "ground_station":
            if self.reference_body is None:
                raise ValueError(f"ground station {self.node_id!r} requires reference_body")
            if self.ground_scheduling is None:
                raise ValueError(f"ground station {self.node_id!r} requires ground_scheduling")
            if self.surface_position is None:
                raise ValueError(f"ground station {self.node_id!r} requires surface_position")
        elif self.ground_scheduling is not None:
            raise ValueError(f"non-ground node {self.node_id!r} must not set ground_scheduling")
        if self.kind == "satellite" and self.central_body is None:
            raise ValueError(f"satellite {self.node_id!r} requires central_body")
        if self.kind == "satellite" and self.orbit is None:
            raise ValueError(f"satellite {self.node_id!r} requires orbit facts")
        if self.kind != "satellite" and self.orbit is not None:
            raise ValueError(f"non-satellite node {self.node_id!r} must not set orbit")
        if self.kind != "satellite" and (self.plane is not None or self.slot is not None):
            raise ValueError(f"non-satellite node {self.node_id!r} must not set plane/slot")
        return self


class ResolvedEndpoint(BaseModel):
    """A link-rule endpoint after selector resolution to concrete runtime node IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: NonEmptyReference
    terminal_role: TerminalRole
    terminal_medium: TerminalMediumLiteral | None = None
    terminal_id: NonEmptyReference | None = None
    min_elevation_deg: float | None = Field(default=None, ge=-90.0, le=90.0, allow_inf_nan=False)
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
    tags: tuple[NonEmptyReference, ...] = ()


class SidBlock(BaseModel):
    """The disjoint segment-routing SID block allocated to one routing domain.

    Segment identity scopes node names; SR capability scopes SID allocation.
    A session can have routed domains that do not run segment routing, and
    those domains must not receive prefix-SID indices.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain_id: NonEmptyReference
    node_ids: tuple[NonEmptyReference, ...] = Field(min_length=1)
    sid_start: int = Field(ge=0)
    sid_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_range(self) -> SidBlock:
        if self.sid_end < self.sid_start:
            raise ValueError(
                f"SID block for routing domain {self.domain_id!r} is reversed: "
                f"sid_end {self.sid_end} < sid_start {self.sid_start}"
            )
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError(f"SID block for routing domain {self.domain_id!r} has duplicate nodes")
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
    bodies: tuple[ResolvedBodyFacts, ...]
    link_rules: tuple[ResolvedLinkRule, ...]
    link_candidates: tuple[ResolvedLinkCandidate, ...] = ()
    routing_domains: tuple[ResolvedRoutingDomain, ...] = ()
    sid_blocks: tuple[SidBlock, ...]
    simulation: Simulation | None = None
    routing: Routing | None = None
    dispatch: Dispatch | None = None
    addressing: Addressing | None = None
    ephemeris: ResolvedEphemeris | None = None
    time: TimeConfig | None = None
    source_context: SourceContext

    @model_validator(mode="after")
    def _validate_consistency(self) -> ResolvedSession:
        ids = [n.node_id for n in self.nodes]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate runtime node_id(s): {dupes}")
        node_segment = {n.node_id: n.segment_id for n in self.nodes}
        # A node answers for its segment plus every placement group that placed
        # its site — shared sites are members of several groups by design.
        node_labels = {n.node_id: {n.segment_id, *n.placement_groups} for n in self.nodes}

        # Loopbacks are router identity: one address, one node, per family.
        for family in ("ipv4", "ipv6"):
            owners: dict[str, str] = {}
            for n in self.nodes:
                if n.interfaces is None:
                    continue
                value = getattr(n.interfaces.lo0, family)
                if value is None:
                    continue
                address = value.split("/")[0]
                if address in owners:
                    raise ValueError(
                        f"duplicate lo0 {family} address {address!r}: "
                        f"{owners[address]!r} and {n.node_id!r}"
                    )
                owners[address] = n.node_id

        body_ids = [body.body_id for body in self.bodies]
        if len(set(body_ids)) != len(body_ids):
            dupes = sorted({body_id for body_id in body_ids if body_ids.count(body_id) > 1})
            raise ValueError(f"duplicate resolved body primitive(s): {dupes}")
        resolved_bodies = set(body_ids)
        active_bodies = {
            body
            for node in self.nodes
            for body in (node.central_body, node.reference_body)
            if body is not None
        }
        missing_bodies = sorted(active_bodies - resolved_bodies)
        if missing_bodies:
            raise ValueError(
                "resolved session is missing body primitive facts for active body/bodies: "
                f"{missing_bodies}"
            )

        domain_ids = [domain.domain_id for domain in self.routing_domains]
        if len(set(domain_ids)) != len(domain_ids):
            dupes = sorted({d for d in domain_ids if domain_ids.count(d) > 1})
            raise ValueError(f"duplicate routing domain id(s): {dupes}")
        sr_domain_ids = {
            domain.domain_id
            for domain in self.routing_domains
            if "segment_routing" in domain.capabilities
        }
        sid_domain_ids = [b.domain_id for b in self.sid_blocks]
        if len(set(sid_domain_ids)) != len(sid_domain_ids):
            raise ValueError("duplicate routing domain in sid_blocks")
        ghost_blocks = sorted(s for s in sid_domain_ids if s not in sr_domain_ids)
        if ghost_blocks:
            raise ValueError(
                "sid_blocks name routing domain(s) without segment_routing capability: "
                f"{ghost_blocks}"
            )
        missing_blocks = sorted(sr_domain_ids - set(sid_domain_ids))
        if missing_blocks:
            raise ValueError(f"SR routing domain(s) missing sid_blocks: {missing_blocks}")
        for block in self.sid_blocks:
            missing = sorted(node_id for node_id in block.node_ids if node_id not in node_segment)
            if missing:
                raise ValueError(
                    f"SID block for routing domain {block.domain_id!r} references "
                    f"unknown node(s): {missing}"
                )
        # SID blocks must be disjoint — overlapping ranges defeat per-segment SID
        # allocation and silently corrupt forwarding.
        ordered = sorted(self.sid_blocks, key=lambda b: b.sid_start)
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if cur.sid_start <= prev.sid_end:
                raise ValueError(
                    f"SID blocks overlap: {prev.domain_id!r} "
                    f"[{prev.sid_start}..{prev.sid_end}] and {cur.domain_id!r} "
                    f"[{cur.sid_start}..{cur.sid_end}]"
                )

        rule_ids = [r.rule_id for r in self.link_rules]
        if len(set(rule_ids)) != len(rule_ids):
            dupes = sorted({r for r in rule_ids if rule_ids.count(r) > 1})
            raise ValueError(f"duplicate link rule id(s): {dupes}")
        for rule in self.link_rules:
            for endpoint in rule.endpoints:
                missing = [nid for nid in endpoint.node_ids if nid not in node_segment]
                if missing:
                    raise ValueError(
                        f"link rule {rule.rule_id!r} endpoint references unknown "
                        f"node_id(s): {missing}"
                    )
                # An endpoint claims one segment label; every node it lists
                # must carry that label (segment or placement group — no
                # membership in unrelated segments).
                foreign = sorted(
                    nid for nid in endpoint.node_ids if endpoint.segment_id not in node_labels[nid]
                )
                if foreign:
                    raise ValueError(
                        f"link rule {rule.rule_id!r} endpoint segment "
                        f"{endpoint.segment_id!r} contains node(s) from another "
                        f"segment: {foreign}"
                    )
        candidate_pairs_by_rule: set[tuple[str, tuple[str, str]]] = set()
        for candidate in self.link_candidates:
            missing = [
                node_id
                for node_id in (candidate.node_a, candidate.node_b)
                if node_id not in node_segment
            ]
            if missing:
                raise ValueError(
                    f"link candidate {candidate.rule_id!r} references unknown node(s): {missing}"
                )
            if candidate.rule_id not in rule_ids:
                raise ValueError(
                    f"link candidate references unknown link rule {candidate.rule_id!r}"
                )
            key = (candidate.rule_id, candidate.pair)
            if key in candidate_pairs_by_rule:
                raise ValueError(
                    f"duplicate link candidate for rule {candidate.rule_id!r}: {candidate.pair}"
                )
            candidate_pairs_by_rule.add(key)

        for domain in self.routing_domains:
            missing = sorted(node_id for node_id in domain.node_ids if node_id not in node_segment)
            if missing:
                raise ValueError(
                    f"routing domain {domain.domain_id!r} references unknown node(s): {missing}"
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

    def node_index_by_node_id(self) -> dict[str, int]:
        """Resolution-order index for every node — the session's only
        globally-unique numeric node identity.

        Consumers that need a compact unique number (FRR system IDs, future
        per-node identity encodings) read this; nothing may derive identity
        from per-segment facts like plane/slot or from enumeration the
        consumer performs itself.
        """
        return {node.node_id: index for index, node in enumerate(self.nodes)}

    def effective_ground_min_elevation_by_gs(self) -> dict[str, float]:
        """The single derivation of each ground station's effective elevation
        mask: per access rule, the max of the matching terminal blocks' masks
        and the rule endpoint's declared mask, max-combined across rules.

        OME enforcement and VS-API display both read this — two derivations
        of the same mask is how the UI ends up showing a constraint the
        allocator does not enforce.
        """
        result: dict[str, float] = {}
        node_by_id = {node.node_id: node for node in self.nodes}
        for rule in self.link_rules:
            if rule.kind != "access" or not rule.enabled:
                continue
            for endpoint in rule.endpoints:
                for node_id in endpoint.node_ids:
                    node = node_by_id[node_id]
                    if node.kind != "ground_station":
                        continue
                    terminal_masks = [
                        block.min_elevation_deg
                        for block in node.terminal_inventory
                        if block.endpoint_role == endpoint.terminal_role
                        and (
                            endpoint.terminal_medium is None
                            or block.medium == endpoint.terminal_medium
                        )
                        and block.min_elevation_deg is not None
                    ]
                    masks = [
                        value
                        for value in (*terminal_masks, endpoint.min_elevation_deg)
                        if value is not None
                    ]
                    if not masks:
                        raise ValueError(
                            f"no resolved min_elevation_deg for access endpoint {node_id}"
                        )
                    effective = max(float(value) for value in masks)
                    result[node_id] = max(result.get(node_id, effective), effective)
        return result

    def ground_index_by_node_id(self) -> dict[str, int]:
        """Resolution-order index over ground stations (wiring-manifest fact).

        The Node Agent manifest contract requires gs_index; this is its only
        derivation — consumers must not enumerate ground nodes themselves.
        """
        return {
            node.node_id: index
            for index, node in enumerate(n for n in self.nodes if n.kind == "ground_station")
        }

    def sid_index_by_node_id(self) -> dict[str, int]:
        """Return deterministic FRR prefix-SID indices for every resolved node."""
        result: dict[str, int] = {}
        for block in sorted(self.sid_blocks, key=lambda item: item.domain_id):
            ordered_nodes = tuple(sorted(block.node_ids))
            expected_count = block.sid_end - block.sid_start + 1
            if expected_count != len(ordered_nodes):
                raise ValueError(
                    f"SID block for routing domain {block.domain_id!r} has {expected_count} index(es) "
                    f"for {len(ordered_nodes)} node(s)"
                )
            for offset, node_id in enumerate(ordered_nodes):
                result[node_id] = block.sid_start + offset
        return result

    def link_interface_map(self) -> dict[tuple[str, str], tuple[str, str]]:
        """Return concrete interface names keyed by canonical node pair."""
        return {
            candidate.pair: (candidate.interface_a, candidate.interface_b)
            for candidate in self.link_candidates
        }

    def link_bandwidth_map(self) -> dict[tuple[str, str], float]:
        """Return concrete bottleneck bandwidth keyed by canonical node pair."""
        return {candidate.pair: candidate.bandwidth_mbps for candidate in self.link_candidates}

    def ground_candidate_satellites_by_gs(self) -> dict[str, tuple[str, ...]]:
        """Return access candidate satellites keyed by ground station node id."""
        ground_ids = {node.node_id for node in self.nodes if node.kind == "ground_station"}
        satellite_ids = {node.node_id for node in self.nodes if node.kind == "satellite"}
        result: dict[str, list[str]] = {}
        for candidate in self.link_candidates:
            if candidate.kind != "access":
                continue
            left_ground = candidate.node_a in ground_ids
            right_ground = candidate.node_b in ground_ids
            if left_ground == right_ground:
                raise ValueError(
                    f"access candidate {candidate.pair} must contain exactly one ground station"
                )
            gs_id = candidate.node_a if left_ground else candidate.node_b
            sat_id = candidate.node_b if left_ground else candidate.node_a
            if sat_id not in satellite_ids:
                raise ValueError(f"access candidate {candidate.pair} has no satellite endpoint")
            result.setdefault(gs_id, []).append(sat_id)
        return {gs_id: tuple(sorted(set(sats))) for gs_id, sats in sorted(result.items())}
