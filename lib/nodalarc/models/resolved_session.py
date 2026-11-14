# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""ResolvedSession — the frozen runtime view produced by session resolution.

This module defines the authoritative object that the resolver will hand to OME,
Scheduler, Operator, VS-API, and MI. The model self-defends the
runtime truth it can validate locally: immutable config, concrete node identity,
materialized terminal inventory, disjoint SID blocks, and resolved link-rule
node sets.
"""

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import FrameBodyName, SupportedSurfaceBody
from nodalarc.model_validation import ADDRESS_FAMILIES, AddressFamily, NonEmptyReference
from nodalarc.models.catalog import ForwardingClass, MountRole
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import (
    LinkLabel,
    LinkRuleConstraints,
    LinkTopology,
    TopologyMode,
)
from nodalarc.models.segment_session import (
    LINK_STATE_PROTOCOLS,
    OSPF_BACKBONE_AREA,
    Addressing,
    AreaAssignment,
    Dispatch,
    ExportRule,
    Routing,
    RoutingBoundary,
    RoutingCapability,
    RoutingProtocol,
    RoutingTimers,
    SessionMeta,
    Simulation,
    TimeConfig,
)
from nodalarc.models.segments import GroundScheduling, SegmentClock
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.tle import tle_epoch_unix, tle_norad_id, validate_tle_pair

# Where a node is placed: fixed to a body's surface, or in orbit about a body. Every
# difference between the two that the runtime acts on is a difference of placement: a surface
# node has a position and a horizon, an orbiting node has an orbit.
type PlacementFrame = Literal["orbit", "surface"]
# What the page calls a node of each placement frame.
type NodeKind = Literal["satellite", "ground_station"]


def node_kind_of(placement_frame: PlacementFrame) -> NodeKind:
    """The page's name for a node of one placement frame."""
    return "satellite" if placement_frame == "orbit" else "ground_station"


# The authored object a resolution refusal names, when its raise site knows it.
type ResolutionSubjectKind = Literal["segment", "link_rule", "node"]


class InterfaceRates(NamedTuple):
    """The rates of the terminal behind one WAN interface."""

    transmit_mbps: float
    receive_mbps: float


# A node's role in routing. A router forwards between subnets and
# participates in at least one routing instance; a host forwards nothing; a
# node that forwards and participates in no instance forwards only between
# its connected subnets.
type NodeRole = Literal["router", "host", "forwarding_only"]


@dataclass(frozen=True)
class IsisInstanceAreas:
    """A router's areas in one IS-IS instance: the area addresses of its NET.

    IS-IS interfaces carry no area; two routers of one instance whose area
    addresses share none form only a Level 2 adjacency.
    """

    domain_id: str
    area_addresses: tuple[str, ...]


@dataclass(frozen=True)
class OspfInstanceAreas:
    """A router's areas in one OSPF instance: the area of each interface.

    ``interface_areas`` maps each interface the router runs the instance on
    to its area; the loopback's area is separate.
    """

    domain_id: str
    loopback_area: str
    interface_areas: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "interface_areas", MappingProxyType(dict(self.interface_areas)))

    @property
    def areas(self) -> tuple[str, ...]:
        """Every area the router has an interface in, in numeric order."""
        return tuple(
            sorted(
                {self.loopback_area, *self.interface_areas.values()},
                key=lambda area: int(ipaddress.IPv4Address(area)),
            )
        )

    @property
    def area_border(self) -> bool:
        """Whether the router has interfaces in the backbone and another area."""
        areas = self.areas
        return OSPF_BACKBONE_AREA in areas and len(areas) > 1


InstanceAreas = IsisInstanceAreas | OspfInstanceAreas


class InstanceLink(NamedTuple):
    """A possible adjacency of one routing instance: two participants and the
    interfaces each can run the instance on toward the other.

    A fixed link names one interface on each end; an access link names every
    access interface of each end in the instance, since the link can form on
    any of them; a segment names each member's segment interface.
    """

    node_a: str
    interfaces_a: tuple[str, ...]
    node_b: str
    interfaces_b: tuple[str, ...]


class BoundaryImport(NamedTuple):
    """One router receiving a ``static_ip`` boundary export into an instance.

    ``node_id`` participates in the export's ``to`` instance and installs the
    exported prefixes toward ``peer_id``, which participates in its ``from``
    instance. The record names no interface: a static route is router
    configuration and does not follow from which interface or terminal joins
    the two routers.
    """

    boundary: RoutingBoundary
    export: ExportRule
    node_id: str
    peer_id: str


def router_node_ids(
    nodes: tuple[ResolvedNode, ...], routing_domains: tuple[ResolvedRoutingDomain, ...]
) -> frozenset[str]:
    """The routers: nodes that forward (``routed``) and participate in an instance."""
    participants = {node_id for domain in routing_domains for node_id in domain.node_ids}
    return frozenset(
        node.node_id
        for node in nodes
        if node.forwarding == "routed" and node.node_id in participants
    )


type TerminalMediumLiteral = Literal["rf", "optical"]


class ResolvedOrbitFacts(BaseModel):
    """Runtime orbital facts for one resolved space node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orbit_id: NonEmptyReference
    central_body: FrameBodyName
    epoch: NonEmptyReference
    # "crtbp" is structurally representable so the runtime-support gate can
    # reject it with a typed UnsupportedFeature instead of an opaque schema
    # error; no production runtime path consumes it yet.
    propagator: Literal["two_body", "j2_mean_elements", "sgp4_tle", "crtbp"]
    semi_major_axis_km: float = Field(gt=0, allow_inf_nan=False)
    eccentricity: float = Field(ge=0, lt=1, allow_inf_nan=False)
    inclination_deg: float = Field(allow_inf_nan=False)
    raan_deg: float = Field(allow_inf_nan=False)
    argument_of_perigee_deg: float = Field(allow_inf_nan=False)
    mean_anomaly_deg: float = Field(allow_inf_nan=False)
    tle_line_1: str | None = None
    tle_line_2: str | None = None
    norad_id: int | None = None

    @model_validator(mode="after")
    def _tle_scope(self) -> ResolvedOrbitFacts:
        tle_values = (self.tle_line_1, self.tle_line_2, self.norad_id)
        if self.propagator != "sgp4_tle":
            if any(value is not None for value in tle_values):
                raise ValueError("non-SGP4 orbit facts must not carry TLE fields")
            return self
        if self.tle_line_1 is None or self.tle_line_2 is None or self.norad_id is None:
            raise ValueError("sgp4_tle orbit facts require both TLE lines and norad_id")
        validate_tle_pair(self.tle_line_1, self.tle_line_2)
        if tle_norad_id(self.tle_line_1) != self.norad_id:
            raise ValueError("sgp4_tle norad_id must match TLE line 1")
        parsed_epoch = datetime.fromisoformat(
            f"{self.epoch[:-1]}+00:00" if self.epoch.endswith("Z") else self.epoch
        )
        if parsed_epoch.tzinfo is None or parsed_epoch.utcoffset() is None:
            raise ValueError("sgp4_tle epoch must include an explicit UTC offset")
        if abs(parsed_epoch.timestamp() - tle_epoch_unix(self.tle_line_1)) > 1e-6:
            raise ValueError("sgp4_tle epoch must match TLE line 1")
        return self


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
    the source file. The catalog requires every physical fact a block carries
    (capacity, range, elevation limit, field of regard, tracking rate and both
    rates), so every block carries them. ``boresight`` exists exactly on access
    terminals.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    terminal_id: NonEmptyReference
    owner_node_id: NonEmptyReference
    endpoint_role: MountRole
    medium: TerminalMediumLiteral  # rf | optical
    source_terminal_id: NonEmptyReference | None = None
    link_role: NonEmptyReference | None = None
    count: int = Field(gt=0)
    # Where the installed count was stated: the node model, or the site installation. A
    # refusal names it.
    count_stated_at: str | None = None
    tracking_capacity: int = Field(gt=0)
    max_range_km: float = Field(gt=0, allow_inf_nan=False)
    # The lowest elevation the terminal can point to: its catalog pointing range,
    # or the narrower range its site installation states.
    min_elevation_deg: float = Field(ge=-90.0, le=90.0, allow_inf_nan=False)
    # The lowest elevation the site's installation states for this terminal;
    # None where no installation states one. Of the two, only this is a ground
    # station's elevation floor: a catalog pointing range describes the
    # hardware wherever it is mounted, never a station's horizon.
    installed_min_elevation_deg: float | None = Field(
        default=None, ge=-90.0, le=90.0, allow_inf_nan=False
    )
    field_of_regard_deg: float = Field(gt=0, le=360.0, allow_inf_nan=False)
    tracking_rate_deg_s: float = Field(gt=0, allow_inf_nan=False)
    # The terminal's own rates, independent of any peer: what it can send and
    # what it can receive.
    transmit_mbps: float = Field(gt=0, allow_inf_nan=False)
    receive_mbps: float = Field(gt=0, allow_inf_nan=False)
    boresight: TerminalBoresight | SatGroundTerminalBoresight | None = None
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
    """Numbered interfaces allocated by the resolver.

    `ethernet` maps interface name to addresses: a node environment's own
    declared port ids, or a mounted payload's single attach-named
    interface. Every address is allocated on its segment's allocated
    subnet; nothing here is authored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo0: ResolvedInterfaceAddress
    ethernet: dict[NonEmptyReference, ResolvedInterfaceAddress] = Field(default_factory=dict)


class ResolvedOriginatedPrefixes(BaseModel):
    """Concrete routing-injection facts, resolved from symbolic intent.

    Authored origination names segments; resolution replaces each name with
    the segment's allocated subnet (and `default` with the default route),
    so every downstream consumer reads literal prefixes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ipv4: tuple[NonEmptyReference, ...] | None = None
    ipv6: tuple[NonEmptyReference, ...] | None = None


class ResolvedSegmentMember(BaseModel):
    """One environment attached to a resolved Ethernet segment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: NonEmptyReference
    # The kernel interface name inside the member's environment.
    interface: NonEmptyReference


class ResolvedEthernetSegment(BaseModel):
    """One allocated Ethernet segment: a site LAN or a carried bus.

    `scope_id` names the owner (a site id, or a carrier's runtime node id),
    `segment_id` the owner's declared segment. Subnets and membership are
    allocation facts the substrate wires verbatim. Every segment is IPv4;
    `ipv6_subnet` is present exactly when a member originates the segment
    into IPv6, and then every member holds an IPv6 address on it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_id: NonEmptyReference
    segment_id: NonEmptyReference
    ipv4_subnet: NonEmptyReference
    ipv6_subnet: NonEmptyReference | None = None
    members: tuple[ResolvedSegmentMember, ...] = Field(min_length=1)


class ResolvedHostAttachment(BaseModel):
    """Substrate-owned attachment facts for one host-forwarding node.

    Derived at resolution: the host's segment address is its allocated
    assignment on the segment its interface joins, and its gateway is the
    router on that segment with the lowest node id. Host attachment is substrate
    configuration — the platform acting as the network's address authority,
    the way DHCP would — never a protocol-derived forwarding decision. The
    Node Agent applies these facts at wiring time so the host's containers
    need no networking capability of their own. The IPv6 address and
    gateway are present exactly when the host's segment is IPv6.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interface: NonEmptyReference
    ipv4: NonEmptyReference
    gateway_ipv4: NonEmptyReference
    ipv6: NonEmptyReference | None = None
    gateway_ipv6: NonEmptyReference | None = None
    gateway_node_id: NonEmptyReference

    @model_validator(mode="after")
    def _ipv6_paired(self) -> ResolvedHostAttachment:
        if (self.ipv6 is None) != (self.gateway_ipv6 is None):
            raise ValueError("host attachment ipv6 and gateway_ipv6 are present together")
        return self


class ResolvedWanInterface(BaseModel):
    """Derived unnumbered WAN interface created from a terminal mount."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyReference
    owner_node_id: NonEmptyReference
    terminal_id: NonEmptyReference
    borrows: Literal["lo0"] = "lo0"


class ResolvedRoutingDomain(BaseModel):
    """One routing domain after selector resolution.

    ``node_ids`` are its participants: the selected nodes that run its
    protocol. A selected node that runs no routing is inside the domain
    without participating.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain_id: NonEmptyReference
    protocol: RoutingProtocol
    node_ids: tuple[NonEmptyReference, ...] = Field(min_length=1)
    capabilities: tuple[RoutingCapability, ...] = ()
    area_assignment: AreaAssignment | None = None
    # Effective timer values — defaults applied at resolution, so every
    # consumer reads one populated truth and templates carry no fallbacks.
    timers: RoutingTimers = RoutingTimers()

    @model_validator(mode="after")
    def _unique_node_ids(self) -> ResolvedRoutingDomain:
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError(f"routing domain {self.domain_id!r} contains duplicate node ids")
        return self

    def area_id_for(self, node: ResolvedNode) -> str:
        """The routing area of one member router, in its protocol's area format.

        IS-IS areas are area addresses (49.0001) and OSPF areas dotted IDs
        (0.0.0.0). A domain with no assignment, or a flat one, is one area:
        the assignment's ground-station area when it names one, else the
        protocol's first area. Only IS-IS and OSPF domains have areas.
        """
        if self.protocol not in LINK_STATE_PROTOCOLS:
            raise ValueError(
                f"routing domain {self.domain_id!r} runs {self.protocol}, which has no areas"
            )
        if node.node_id not in self.node_ids:
            raise ValueError(
                f"node {node.node_id!r} is not a member of routing domain {self.domain_id!r}"
            )
        assignment = self.area_assignment
        is_ospf = self.protocol == "ospf"
        first_area = OSPF_BACKBONE_AREA if is_ospf else "49.0001"
        if assignment is None or assignment.strategy == "flat":
            return (
                assignment.gs_area_id
                if assignment is not None and assignment.gs_area_id
                else first_area
            )
        if node.placement_frame != "orbit":
            if assignment.strategy == "explicit":
                matches = [
                    mapping.area_id
                    for mapping in assignment.assignments or ()
                    if mapping.ground_stations == "all"
                    or (
                        isinstance(mapping.ground_stations, tuple)
                        and node.local_node_id in mapping.ground_stations
                    )
                ]
                if len(matches) > 1:
                    raise ValueError(
                        f"explicit area assignment in domain {self.domain_id!r} maps ground "
                        f"station {node.local_node_id!r} more than once"
                    )
                if matches:
                    return matches[0]
            return assignment.gs_area_id or first_area
        if node.plane is None:
            raise ValueError(f"node {node.node_id!r} is missing plane for area assignment")
        if assignment.strategy == "per_plane":
            return f"0.0.0.{node.plane + 1}" if is_ospf else f"49.{node.plane + 1:04d}"
        if assignment.strategy == "stripe":
            if assignment.planes_per_stripe is None:
                raise ValueError("stripe area assignment requires planes_per_stripe")
            stripe_index = node.plane // assignment.planes_per_stripe
            return f"0.0.0.{stripe_index + 1}" if is_ospf else f"49.{stripe_index + 1:04d}"
        if assignment.strategy == "explicit":
            for mapping in assignment.assignments or ():
                if mapping.planes is not None and node.plane in mapping.planes:
                    return mapping.area_id
            raise ValueError(
                f"explicit area assignment in domain {self.domain_id!r} has no plane mapping "
                f"for node {node.node_id!r}"
            )
        raise ValueError(f"unsupported area assignment strategy {assignment.strategy!r}")


class ResolvedEphemerisFile(BaseModel):
    """One platform ephemeris data file selected by the session's bodies.

    The platform chooses ephemeris data from the bodies a session uses; the
    session does not declare it. This record ties every result to the data
    that produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_id: NonEmptyReference
    filename: NonEmptyReference
    sha256: NonEmptyReference


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


def assigns_terminals(kind: LinkLabel, topology_mode: TopologyMode) -> bool:
    """Whether a rule's candidates have their terminals assigned per pass.

    A ``visible_candidates`` rule chooses among its usable candidates as the geometry changes,
    so a node's terminal serves one peer now and another later: no interface can be bound to
    a pair. An access rule is assigned in every mode. Every other rule fixes its pairs, and
    each pair keeps the interfaces the resolver bound to it.
    """
    return kind == "access" or topology_mode == "visible_candidates"


# A link whose one-way delay can exceed the platform's long-delay bound is a
# long-delay link; every other link is a local link. The resolver classifies
# each candidate once, from every delay the candidate can have.
type LinkDelayClass = Literal["local", "long_delay"]


class ResolvedLinkCandidate(BaseModel):
    """One declared candidate pair.

    A candidate has one of two forms. A fixed candidate carries the interface the resolver
    bound to it at each node. A candidate whose terminals are assigned per pass carries none:
    OME assigns one of each node's selected terminals when it schedules the pair. A rule that
    chooses among visible candidates as the geometry changes (``visible_candidates``) resolves
    to the assigned form, as does every access rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: NonEmptyReference
    kind: LinkLabel
    # Endpoint-ordered (parallel to endpoint_segments) — a link may join two
    # different terminal classes, e.g. a LEO isl head to a HEO crosslink head.
    terminal_roles: tuple[MountRole, MountRole]
    terminal_medium: TerminalMediumLiteral | None = None
    node_a: NonEmptyReference
    node_b: NonEmptyReference
    interface_a: NonEmptyReference | None = None
    interface_b: NonEmptyReference | None = None
    topology_mode: TopologyMode
    priority: int = Field(ge=0)
    endpoint_segments: tuple[NonEmptyReference, NonEmptyReference]
    delay_class: LinkDelayClass
    # The longest one-way delay the candidate can have, in seconds, bounded at resolution with
    # its class: the delay each of its queues is sized for in the per-server memory budget.
    longest_delay_s: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _candidate_invariants(self) -> ResolvedLinkCandidate:
        if self.node_a == self.node_b:
            raise ValueError(f"link candidate {self.rule_id!r} has identical endpoints")
        interfaces = (self.interface_a, self.interface_b)
        if assigns_terminals(self.kind, self.topology_mode):
            if any(interface is not None for interface in interfaces):
                raise ValueError(
                    f"candidate {self.rule_id!r} has its terminals assigned per pass and must "
                    "not carry fixed interfaces"
                )
        elif any(interface is None for interface in interfaces):
            raise ValueError(
                f"fixed link candidate {self.rule_id!r} requires both endpoint interfaces"
            )
        return self

    @property
    def assigned_terminals(self) -> bool:
        """Whether OME assigns the candidate's terminals for each pass."""
        return assigns_terminals(self.kind, self.topology_mode)

    @property
    def pair(self) -> tuple[str, str]:
        return (self.node_a, self.node_b)

    @property
    def fixed_interfaces(self) -> tuple[str, str]:
        """Return the resolver-owned interfaces of a fixed candidate."""
        if self.interface_a is None or self.interface_b is None:
            raise ValueError(
                f"candidate {self.rule_id!r} has its terminals assigned per pass; it has no "
                "fixed interfaces"
            )
        return self.interface_a, self.interface_b


class ResolvedNode(BaseModel):
    """One runtime node with explicit identity, body/frame, terminals, and policy.

    Carries both ``local_node_id`` (source-segment ID before expansion) and
    ``node_id`` (runtime ID) so no consumer infers identity from string shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: NonEmptyReference
    local_node_id: NonEmptyReference
    segment_id: NonEmptyReference
    namespace: NonEmptyReference
    # Ground nodes: every placement group (ground segment) that placed this
    # node's site. A physical site exists once; placement groups are labels,
    # not namespaces, so one node may belong to several. segment_id holds the
    # first placing group for surfaces that need a single primary label.
    # Empty for space nodes (their segment IS their namespace).
    placement_groups: tuple[NonEmptyReference, ...] = ()
    placement_frame: PlacementFrame
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
    originated_prefixes: ResolvedOriginatedPrefixes | None = None
    # How the node's kernel forwards. A router forwards between subnets and
    # participates in routing: ``routed`` here and a participant of at least
    # one routing domain (``ResolvedSession.routing_domains_for``).
    forwarding: ForwardingClass
    # The effective workload profile and the level that supplied it: the
    # placed node entry, the segment, or the node definition. Resolution
    # refuses a node with no statement at any level; there is no default
    # workload.
    profile: NonEmptyReference
    profile_level: Literal["node", "segment", "node_definition"]
    # Present exactly when forwarding == "host": the derived substrate
    # attachment the Node Agent applies at wiring time.
    host_attachment: ResolvedHostAttachment | None = None
    service_priority: int | None = Field(default=None, gt=0)
    plane: int | None = Field(default=None, ge=0)
    slot: int | None = Field(default=None, ge=0)
    # The scheduling a ground node states, effective after precedence; None for a ground node
    # that states none (NodalArc then chooses its links) and for every space node.
    ground_scheduling: GroundScheduling | None = None
    # Where each stated scheduling field was stated, as (field, place): the segment's apply,
    # a site override, or the site node. A refusal names it.
    scheduling_stated_at: tuple[tuple[str, str], ...] = ()
    clock: SegmentClock = SegmentClock()

    @property
    def address_families(self) -> frozenset[AddressFamily]:
        """The IP address families this node carries, as the session declares them.

        A family is carried when the node holds a loopback or segment
        address in it, or originates prefixes in it: a node originating an
        IPv6 default route routes IPv6 without an IPv6 address of its own.
        Every consumer that enables, configures or routes a family on a node
        reads this one fact.
        """
        carried: set[AddressFamily] = set()
        if self.interfaces is not None:
            for address in (self.interfaces.lo0, *self.interfaces.ethernet.values()):
                carried.update(
                    family for family in ADDRESS_FAMILIES if getattr(address, family) is not None
                )
        if self.originated_prefixes is not None:
            carried.update(
                family
                for family in ADDRESS_FAMILIES
                if getattr(self.originated_prefixes, family) is not None
            )
        return frozenset(carried)

    def wan_terminal(self, interface: str) -> ResolvedTerminalBlock:
        """The terminal block behind one of this node's WAN interfaces."""
        wan = next((wan for wan in self.wan_interfaces if wan.name == interface), None)
        if wan is None:
            raise KeyError(f"node {self.node_id!r} has no WAN interface {interface!r}")
        return next(
            block for block in self.terminal_inventory if block.terminal_id == wan.terminal_id
        )

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
            if block.endpoint_role == "access":
                if self.placement_frame == "surface" and not isinstance(
                    block.boresight, TerminalBoresight
                ):
                    raise ValueError(
                        f"ground node {self.node_id!r} access terminal "
                        f"{block.terminal_id!r} requires a ground boresight"
                    )
                # The body a satellite access terminal points at is the body of the stations its
                # access rules select (resolver-derived); it may differ from the central body.
                if self.placement_frame == "orbit" and not isinstance(
                    block.boresight, SatGroundTerminalBoresight
                ):
                    raise ValueError(
                        f"satellite {self.node_id!r} access terminal "
                        f"{block.terminal_id!r} requires a spacecraft nadir boresight"
                    )
            elif block.boresight is not None:
                raise ValueError(
                    f"node {self.node_id!r} non-access terminal {block.terminal_id!r} "
                    "must not carry an access boresight"
                )
        for wan in self.wan_interfaces:
            if wan.terminal_id not in seen:
                raise ValueError(
                    f"node {self.node_id!r} WAN interface {wan.name!r} names terminal "
                    f"{wan.terminal_id!r}, which is not in its terminal inventory"
                )
        if self.placement_frame == "surface":
            if self.reference_body is None:
                raise ValueError(f"ground station {self.node_id!r} requires reference_body")
            if self.surface_position is None:
                raise ValueError(f"ground station {self.node_id!r} requires surface_position")
        elif self.ground_scheduling is not None:
            raise ValueError(f"non-ground node {self.node_id!r} must not set ground_scheduling")
        if self.placement_frame == "orbit" and self.central_body is None:
            raise ValueError(f"satellite {self.node_id!r} requires central_body")
        if self.placement_frame == "orbit" and self.orbit is None:
            raise ValueError(f"satellite {self.node_id!r} requires orbit facts")
        if self.placement_frame != "orbit" and self.orbit is not None:
            raise ValueError(f"non-satellite node {self.node_id!r} must not set orbit")
        if self.placement_frame != "orbit" and (self.plane is not None or self.slot is not None):
            raise ValueError(f"non-satellite node {self.node_id!r} must not set plane/slot")
        return self


class ResolvedEndpoint(BaseModel):
    """A link-rule endpoint after selector resolution to concrete runtime node IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: NonEmptyReference
    terminal_role: MountRole
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

    def matches_terminal(self, block: ResolvedTerminalBlock) -> bool:
        """Return whether one resolved mount satisfies this endpoint."""
        return (
            block.endpoint_role == self.terminal_role
            and (self.terminal_medium is None or block.medium == self.terminal_medium)
            and (self.terminal_id is None or block.terminal_id == self.terminal_id)
        )


@dataclass(frozen=True, slots=True)
class ResolvedTerminalSelection:
    """One mount a rule that assigns terminals selects on a node, the node's terminal index of
    each of its interfaces and the interface behind each index, in the same order."""

    block: ResolvedTerminalBlock
    interface_indices: tuple[int, ...]
    interface_names: tuple[str, ...]


class ResolvedLinkRule(BaseModel):
    """A link rule after selector resolution. Candidate generation starts here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: NonEmptyReference
    kind: LinkLabel
    enabled: bool
    endpoints: tuple[ResolvedEndpoint, ResolvedEndpoint]
    topology: LinkTopology
    constraints: LinkRuleConstraints | None = None
    tags: tuple[NonEmptyReference, ...] = ()

    @property
    def assigns_terminals(self) -> bool:
        """Whether the rule's candidates have their terminals assigned per pass."""
        return assigns_terminals(self.kind, self.topology.mode)


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

    # e.g. "vs_api.deploy", "operator.reconcile", "test".
    origin: NonEmptyReference
    session_path: NonEmptyReference | None = None
    run_id: NonEmptyReference | None = None


class ResolvedSegment(BaseModel):
    """One authored segment as the resolver placed it: identity plus presentation.

    ``display_name`` is presentation metadata for authoring surfaces. It is the
    segment's own authored name, else the referenced catalog object's, else
    the segment id, and the semantic projection does not read it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    kind: Literal["space", "ground"]
    display_name: str
    source_ref: str


class ResolvedSession(BaseModel):
    """The single authoritative runtime view consumed by every service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_mode: IdentityMode
    session: SessionMeta
    segments: tuple[ResolvedSegment, ...] = ()
    nodes: tuple[ResolvedNode, ...]
    bodies: tuple[ResolvedBodyFacts, ...]
    link_rules: tuple[ResolvedLinkRule, ...]
    link_candidates: tuple[ResolvedLinkCandidate, ...] = ()
    routing_domains: tuple[ResolvedRoutingDomain, ...] = ()
    # Every allocated Ethernet segment (site LANs and carried buses) with
    # its membership; the substrate wires these verbatim.
    ethernet_segments: tuple[ResolvedEthernetSegment, ...] = ()
    sid_blocks: tuple[SidBlock, ...]
    simulation: Simulation | None = None
    routing: Routing | None = None
    dispatch: Dispatch | None = None
    addressing: Addressing | None = None
    ephemeris_files: tuple[ResolvedEphemerisFile, ...] = Field(min_length=1)
    # The platform's long-delay bound the candidates were classified against.
    long_delay_bound_s: float = Field(gt=0, allow_inf_nan=False)
    time: TimeConfig
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

        # A segment's families are its members' families on it: an IPv6
        # segment gives every member an IPv6 address, an IPv4-only segment
        # gives none.
        nodes_by_id = {n.node_id: n for n in self.nodes}
        for segment in self.ethernet_segments:
            for member in segment.members:
                node = nodes_by_id.get(member.node_id)
                address = (
                    node.interfaces.ethernet.get(member.interface)
                    if node is not None and node.interfaces is not None
                    else None
                )
                if address is None:
                    raise ValueError(
                        f"segment {segment.scope_id}/{segment.segment_id} member "
                        f"{member.node_id!r} has no address on {member.interface!r}"
                    )
                if address.ipv4 is None or (address.ipv6 is None) != (segment.ipv6_subnet is None):
                    raise ValueError(
                        f"segment {segment.scope_id}/{segment.segment_id} member "
                        f"{member.node_id!r} holds address families that differ from "
                        "the segment's"
                    )
        # A host attaches with exactly the addresses of its attached interface.
        for node in self.nodes:
            attachment = node.host_attachment
            if attachment is None:
                continue
            address = (
                node.interfaces.ethernet.get(attachment.interface)
                if node.interfaces is not None
                else None
            )
            if address is None or (address.ipv4, address.ipv6) != (
                attachment.ipv4,
                attachment.ipv6,
            ):
                raise ValueError(
                    f"host node {node.node_id!r} attachment addresses differ from its "
                    f"{attachment.interface!r} interface addresses"
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

    def instance_areas_by_node(self) -> dict[str, tuple[InstanceAreas, ...]]:
        """Every IS-IS and OSPF participant's areas, per instance in declared order.

        The session assigns areas per router (``area_assignment``), so each
        OSPF interface, the loopback included, takes its router's area, and
        each IS-IS router has one area address. A node absent from the
        mapping participates in no IS-IS or OSPF instance.
        """
        interfaces_by_node = self.domain_interfaces_by_node()
        areas: dict[str, list[InstanceAreas]] = {}
        for domain in self.routing_domains:
            if domain.protocol not in LINK_STATE_PROTOCOLS:
                continue
            for node_id in domain.node_ids:
                node = self.node_by_id(node_id)
                if node is None:
                    raise ValueError(
                        f"routing domain {domain.domain_id!r} names unknown node {node_id!r}"
                    )
                area = domain.area_id_for(node)
                instance_areas: InstanceAreas
                if domain.protocol == "isis":
                    instance_areas = IsisInstanceAreas(domain.domain_id, (area,))
                else:
                    instance_areas = OspfInstanceAreas(
                        domain.domain_id,
                        loopback_area=area,
                        interface_areas=dict.fromkeys(
                            interfaces_by_node[node_id][domain.domain_id], area
                        ),
                    )
                areas.setdefault(node_id, []).append(instance_areas)
        return {node_id: tuple(node_areas) for node_id, node_areas in areas.items()}

    def instance_links(self) -> dict[str, tuple[InstanceLink, ...]]:
        """Each instance's possible adjacencies, keyed by domain id.

        A fixed link, a link on terminals assigned per pass or a shared Ethernet
        segment joins two participants of an instance when each end has an
        interface in the instance toward the other (``domain_interfaces_by_node``).
        A link a ``static_ip`` boundary crosses belongs to no instance.
        """
        interfaces_by_node = self.domain_interfaces_by_node()
        carried = self.candidate_interfaces()
        static_rules = self.static_boundary_rule_ids()
        links: dict[str, list[InstanceLink]] = {
            domain.domain_id: [] for domain in self.routing_domains
        }

        def in_instance(node_id: str, domain_id: str, names: tuple[str, ...]) -> tuple[str, ...]:
            own = interfaces_by_node.get(node_id, {}).get(domain_id, ())
            return tuple(name for name in names if name in own)

        for candidate in self.link_candidates:
            if candidate.rule_id in static_rules:
                continue
            names_a, names_b = carried[candidate.pair]
            for domain_id in links:
                ends = (
                    in_instance(candidate.node_a, domain_id, names_a),
                    in_instance(candidate.node_b, domain_id, names_b),
                )
                if ends[0] and ends[1]:
                    links[domain_id].append(
                        InstanceLink(candidate.node_a, ends[0], candidate.node_b, ends[1])
                    )
        for segment in self.ethernet_segments:
            for domain_id in links:
                members = [
                    member
                    for member in segment.members
                    if in_instance(member.node_id, domain_id, (member.interface,))
                ]
                for index, first in enumerate(members):
                    for second in members[index + 1 :]:
                        links[domain_id].append(
                            InstanceLink(
                                first.node_id,
                                (first.interface,),
                                second.node_id,
                                (second.interface,),
                            )
                        )
        return {domain_id: tuple(items) for domain_id, items in links.items()}

    def area_border_instances_by_node(self) -> dict[str, tuple[str, ...]]:
        """The IS-IS and OSPF instances in which each router is an area border router.

        An OSPF router is one when its interfaces sit in the backbone and
        another area. An IS-IS router is one when a possible adjacency of the
        instance joins it to a router whose area addresses share none with
        its own. Instances follow declared order.
        """
        areas_by_node = self.instance_areas_by_node()
        isis_areas = {
            (node_id, areas.domain_id): set(areas.area_addresses)
            for node_id, node_areas in areas_by_node.items()
            for areas in node_areas
            if isinstance(areas, IsisInstanceAreas)
        }
        border: set[tuple[str, str]] = {
            (node_id, areas.domain_id)
            for node_id, node_areas in areas_by_node.items()
            for areas in node_areas
            if isinstance(areas, OspfInstanceAreas) and areas.area_border
        }
        for domain_id, links in self.instance_links().items():
            for link in links:
                own = isis_areas.get((link.node_a, domain_id))
                peer = isis_areas.get((link.node_b, domain_id))
                if own is not None and peer is not None and not own & peer:
                    border.update({(link.node_a, domain_id), (link.node_b, domain_id)})
        return {
            node_id: tuple(
                domain.domain_id
                for domain in self.routing_domains
                if (node_id, domain.domain_id) in border
            )
            for node_id in sorted({node_id for node_id, _ in border})
        }

    def boundary_imports(self) -> tuple[BoundaryImport, ...]:
        """Every router receiving a ``static_ip`` boundary export, per export.

        The receiving end of ``from: X, to: Y`` over a boundary link is the
        link's end that participates in Y while its peer participates in X.
        """
        if self.routing is None or not self.routing.boundaries:
            return ()
        participants = {domain.domain_id: set(domain.node_ids) for domain in self.routing_domains}
        imports: list[BoundaryImport] = []
        for boundary in self.routing.boundaries:
            if boundary.adapter != "static_ip":
                continue
            for export in boundary.export:
                for candidate in self.link_candidates:
                    if candidate.rule_id != boundary.over:
                        continue
                    ends = (
                        (candidate.node_a, candidate.node_b),
                        (candidate.node_b, candidate.node_a),
                    )
                    for node_id, peer_id in ends:
                        if (
                            node_id in participants[export.to]
                            and peer_id in participants[export.from_]
                        ):
                            imports.append(BoundaryImport(boundary, export, node_id, peer_id))
        return tuple(imports)

    def as_boundary_instances_by_node(self) -> dict[str, tuple[str, ...]]:
        """The IS-IS and OSPF instances each router redistributes boundary exports into.

        Such a router is an autonomous system boundary router of the
        instance. Instances follow declared order.
        """
        protocol_of = {domain.domain_id: domain.protocol for domain in self.routing_domains}
        into: dict[str, set[str]] = {}
        for item in self.boundary_imports():
            if protocol_of[item.export.to] in LINK_STATE_PROTOCOLS:
                into.setdefault(item.node_id, set()).add(item.export.to)
        return {
            node_id: tuple(
                domain.domain_id for domain in self.routing_domains if domain.domain_id in domains
            )
            for node_id, domains in sorted(into.items())
        }

    def node_roles(self) -> dict[str, NodeRole]:
        """Every node's role in routing (``NodeRole``)."""
        routers = router_node_ids(self.nodes, self.routing_domains)
        return {
            node.node_id: (
                "router"
                if node.node_id in routers
                else "host"
                if node.forwarding == "host"
                else "forwarding_only"
            )
            for node in self.nodes
        }

    def routing_domains_for(self, node_id: str) -> tuple[ResolvedRoutingDomain, ...]:
        """The routing domains ``node_id`` participates in, in declared order.

        A participant runs the domain's protocol. A domain may select nodes
        that do not participate, and a router participates in every domain
        one of its interfaces belongs to.
        """
        return tuple(domain for domain in self.routing_domains if node_id in domain.node_ids)

    def domain_interfaces(self, node_id: str) -> dict[str, tuple[str, ...]]:
        """The interfaces of ``node_id`` in each routing domain it participates in.

        ``domain_interfaces_by_node`` states the assignment. A node that
        participates in no domain has none.
        """
        if self.node_by_id(node_id) is None:
            raise ValueError(f"no resolved node {node_id!r}")
        return self.domain_interfaces_by_node().get(node_id, {})

    def domain_interfaces_by_node(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """Each participant's interfaces in each routing domain it participates in.

        A router takes part in a domain through its interfaces, and its
        loopback belongs to every domain it participates in. A fixed link
        belongs to the domains the node shares with the peer on it. An
        interface assigned per pass belongs to the domains the node shares
        with the nodes it can reach over the links it can carry
        (``candidate_interfaces``). A link a ``static_ip`` boundary crosses
        belongs to none, so it gives no interface a domain: an interface that
        can carry only such links runs no instance. An Ethernet segment
        interface belongs to the domains the node shares with the segment's
        other participants, or, when it shares none, to every domain the node
        participates in. Inner keys follow declared domain order; the loopback
        is not listed.
        """
        participation: dict[str, set[str]] = {}
        for domain in self.routing_domains:
            for member in domain.node_ids:
                participation.setdefault(member, set()).add(domain.domain_id)
        static_rules = self.static_boundary_rule_ids()
        assigned: dict[str, dict[str, set[str]]] = {node_id: {} for node_id in participation}
        carried = self.candidate_interfaces()
        for candidate in self.link_candidates:
            if candidate.rule_id in static_rules:
                continue
            names_a, names_b = carried[candidate.pair]
            ends = (
                (candidate.node_a, candidate.node_b, names_a),
                (candidate.node_b, candidate.node_a, names_b),
            )
            for node_id, peer_id, names in ends:
                own = participation.get(node_id)
                if own is None:
                    continue
                shared = own & participation.get(peer_id, set())
                for name in names:
                    assigned[node_id].setdefault(name, set()).update(shared)
        for segment in self.ethernet_segments:
            for member in segment.members:
                own = participation.get(member.node_id)
                if own is None:
                    continue
                peers = {
                    domain_id
                    for other in segment.members
                    if other.node_id != member.node_id
                    for domain_id in participation.get(other.node_id, set())
                }
                assigned[member.node_id][member.interface] = (own & peers) or set(own)
        return {
            node_id: {
                domain.domain_id: tuple(
                    sorted(
                        name for name, domains in interfaces.items() if domain.domain_id in domains
                    )
                )
                for domain in self.routing_domains
                if domain.domain_id in participation[node_id]
            }
            for node_id, interfaces in assigned.items()
        }

    def node_index_by_node_id(self) -> dict[str, int]:
        """Resolution-order index for every node — the session's only
        globally-unique numeric node identity.

        Consumers that need a compact unique number (IS-IS system IDs, future
        per-node identity encodings) read this; nothing may derive identity
        from per-segment facts like plane/slot or from enumeration the
        consumer performs itself.
        """
        return {node.node_id: index for index, node in enumerate(self.nodes)}

    def ground_min_elevation_by_gs_and_rule(self) -> dict[str, dict[str, float]]:
        """Each ground station's effective elevation mask, kept per rule.

        Within one rule the endpoint's declared floor and the floors the
        station's installation states for the matching terminals all constrain
        the same links, so their max is that rule's effective mask. A
        terminal's catalog pointing range is not a floor. Across rules the values describe
        different link populations and must never be silently merged; the
        resolver refuses a station whose rules disagree until masks are
        carried per candidate.
        """
        result: dict[str, dict[str, float]] = {}
        selected = self.selected_access_terminals_by_node()
        node_by_id = {node.node_id: node for node in self.nodes}
        for rule in self.link_rules:
            if rule.kind != "access" or not rule.enabled:
                continue
            for endpoint in rule.endpoints:
                for node_id in endpoint.node_ids:
                    node = node_by_id[node_id]
                    if node.placement_frame != "surface":
                        continue
                    installed_floors = [
                        item.block.installed_min_elevation_deg
                        for item in selected[node_id]
                        if endpoint.matches_terminal(item.block)
                        and item.block.installed_min_elevation_deg is not None
                    ]
                    masks = [
                        value
                        for value in (*installed_floors, endpoint.min_elevation_deg)
                        if value is not None
                    ]
                    if not masks:
                        raise ValueError(
                            f"ground station {node_id!r} in access rule {rule.rule_id!r} "
                            "states no elevation floor"
                        )
                    result.setdefault(node_id, {})[rule.rule_id] = max(
                        float(value) for value in masks
                    )
        return result

    def effective_ground_min_elevation_by_gs(self) -> dict[str, float]:
        """The single derivation of each ground station's effective elevation
        mask. The resolver refuses stations whose access rules produce
        divergent per-rule masks, so the max here collapses values that are
        already equal.

        OME enforcement and VS-API display both read this — two derivations
        of the same mask is how the UI ends up showing a constraint the
        allocator does not enforce.
        """
        return {
            node_id: max(by_rule.values())
            for node_id, by_rule in self.ground_min_elevation_by_gs_and_rule().items()
        }

    def static_boundary_rule_ids(self) -> frozenset[str]:
        """The link rules a ``static_ip`` routing boundary crosses."""
        if self.routing is None or not self.routing.boundaries:
            return frozenset()
        return frozenset(
            boundary.over for boundary in self.routing.boundaries if boundary.adapter == "static_ip"
        )

    def assigned_rule_mounts(self) -> dict[tuple[str, str], frozenset[str]]:
        """The mounts each enabled rule that assigns terminals draws on at each of its nodes,
        keyed by (rule_id, node_id): every mount of the node that an endpoint of the rule
        holding the node matches."""
        node_by_id = {node.node_id: node for node in self.nodes}
        mounts: dict[tuple[str, str], set[str]] = {}
        for rule in self.link_rules:
            if not rule.enabled or not rule.assigns_terminals:
                continue
            for endpoint in rule.endpoints:
                for node_id in endpoint.node_ids:
                    matches = {
                        block.terminal_id
                        for block in node_by_id[node_id].terminal_inventory
                        if endpoint.matches_terminal(block)
                    }
                    if not matches:
                        raise ValueError(
                            f"link rule {rule.rule_id!r} endpoint for {node_id!r} "
                            f"matches no resolved {endpoint.terminal_role} terminal mount"
                        )
                    mounts.setdefault((rule.rule_id, node_id), set()).update(matches)
        return {key: frozenset(value) for key, value in mounts.items()}

    def candidate_interfaces(
        self,
    ) -> dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]]:
        """The interfaces that can carry each candidate at each of its two nodes, by canonical
        pair, in WAN order.

        A fixed candidate is bound to one interface at each node. A candidate whose terminals
        OME assigns per pass can be carried by every interface of the mounts its rule draws
        on at the node, since OME picks one of them at each pass. Every reading of what an
        interface can reach or carry comes from here: its peers, its routing domains and the
        senders into its queue. Rejected alternative: reading a node's assigned interfaces as
        one pool, which gave an interface the peers of rules that draw on another mount, and
        ran a routing instance on an interface that can carry only a ``static_ip`` boundary's
        links.
        """
        rule_mounts = self.assigned_rule_mounts()
        node_by_id = {node.node_id: node for node in self.nodes}
        names: dict[tuple[str, str], tuple[str, ...]] = {}

        def assigned_names(rule_id: str, node_id: str) -> tuple[str, ...]:
            key = (rule_id, node_id)
            if key not in names:
                mounts = rule_mounts[key]
                names[key] = tuple(
                    wan.name
                    for wan in node_by_id[node_id].wan_interfaces
                    if wan.terminal_id in mounts
                )
            return names[key]

        carried: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
        for candidate in self.link_candidates:
            if candidate.assigned_terminals:
                carried[candidate.pair] = (
                    assigned_names(candidate.rule_id, candidate.node_a),
                    assigned_names(candidate.rule_id, candidate.node_b),
                )
            else:
                interface_a, interface_b = candidate.fixed_interfaces
                carried[candidate.pair] = ((interface_a,), (interface_b,))
        return carried

    def assigned_terminal_selections_by_node(
        self,
    ) -> dict[str, tuple[ResolvedTerminalSelection, ...]]:
        """Every mount an enabled rule that assigns terminals selects, by node.

        Selection is derived once from resolved endpoints. A terminal's index is the position
        of its interface in the node's WAN interface list, so every terminal of a node has one
        index whatever its interface is named, and filtering a mount never renumbers another.
        Rejected alternative: the number cut from a ``term`` or ``gnd`` interface name, which
        gave ``gnd2`` and an assigned ``isl2`` of one node the same index.
        """
        selected_ids: dict[str, set[str]] = {}
        for (_rule_id, node_id), mounts in self.assigned_rule_mounts().items():
            selected_ids.setdefault(node_id, set()).update(mounts)

        result: dict[str, tuple[ResolvedTerminalSelection, ...]] = {}
        for node in self.nodes:
            terminal_ids = selected_ids.get(node.node_id)
            if not terminal_ids:
                continue
            interfaces_by_terminal: dict[str, list[tuple[int, str]]] = {}
            for index, interface in enumerate(node.wan_interfaces):
                if interface.terminal_id in terminal_ids:
                    interfaces_by_terminal.setdefault(interface.terminal_id, []).append(
                        (index, interface.name)
                    )

            selections: list[ResolvedTerminalSelection] = []
            for block in node.terminal_inventory:
                if block.terminal_id not in terminal_ids:
                    continue
                interfaces = interfaces_by_terminal.get(block.terminal_id, ())
                indices = tuple(index for index, _name in interfaces)
                if len(indices) != block.count:
                    raise ValueError(
                        f"selected mount {node.node_id}:{block.terminal_id} declares "
                        f"count={block.count}, but owns WAN interface indices {indices}"
                    )
                selections.append(
                    ResolvedTerminalSelection(
                        block=block,
                        interface_indices=indices,
                        interface_names=tuple(name for _index, name in interfaces),
                    )
                )
            missing = sorted(terminal_ids - {item.block.terminal_id for item in selections})
            if missing:
                raise ValueError(
                    f"selected mounts for {node.node_id!r} are missing from its "
                    f"resolved terminal inventory: {missing}"
                )
            result[node.node_id] = tuple(selections)
        return result

    def selected_access_terminals_by_node(
        self,
    ) -> dict[str, tuple[ResolvedTerminalSelection, ...]]:
        """The access mounts among ``assigned_terminal_selections_by_node``: the mounts the
        surface-frame physics reads (a station's sky mask, a satellite's boresight toward a
        body)."""
        result: dict[str, tuple[ResolvedTerminalSelection, ...]] = {}
        for node_id, selections in self.assigned_terminal_selections_by_node().items():
            access = tuple(item for item in selections if item.block.endpoint_role == "access")
            if access:
                result[node_id] = access
        return result

    def assigned_interfaces_by_node(self) -> dict[str, tuple[str, ...]]:
        """Each node's WAN interfaces whose peer OME assigns per pass, in WAN order: every
        interface behind an access mount, and every interface behind a mount that an enabled
        rule which assigns terminals selects. Every node has an entry; a node with none has an
        empty one."""
        selected = {
            node_id: {item.block.terminal_id for item in selections}
            for node_id, selections in self.assigned_terminal_selections_by_node().items()
        }
        result: dict[str, tuple[str, ...]] = {}
        for node in self.nodes:
            mounts = selected.get(node.node_id, set()) | {
                block.terminal_id
                for block in node.terminal_inventory
                if block.endpoint_role == "access"
            }
            result[node.node_id] = tuple(
                wan.name for wan in node.wan_interfaces if wan.terminal_id in mounts
            )
        return result

    def terminal_interface_names(self) -> dict[str, dict[int, str]]:
        """Each node's selected terminal interfaces that OME assigns per pass, by terminal
        index.

        OME names the terminals of a link by index. This is the one lookup that turns a
        node's terminal index into the pod interface the resolver created for it, so no
        consumer builds an interface name from an index.
        """
        return {
            node_id: {
                index: name
                for selection in selections
                for index, name in zip(
                    selection.interface_indices, selection.interface_names, strict=True
                )
            }
            for node_id, selections in self.assigned_terminal_selections_by_node().items()
        }

    def ground_index_by_node_id(self) -> dict[str, int]:
        """Resolution-order index over ground stations (wiring-manifest fact).

        The Node Agent manifest contract requires gs_index; this is its only
        derivation — consumers must not enumerate ground nodes themselves.
        """
        return {
            node.node_id: index
            for index, node in enumerate(n for n in self.nodes if n.placement_frame == "surface")
        }

    def sid_index_by_domain(self) -> dict[str, dict[str, int]]:
        """Deterministic prefix-SID indices of each segment-routing domain's participants.

        A router in several segment-routing domains has one index in each.
        """
        result: dict[str, dict[str, int]] = {}
        for block in sorted(self.sid_blocks, key=lambda item: item.domain_id):
            ordered_nodes = tuple(sorted(block.node_ids))
            expected_count = block.sid_end - block.sid_start + 1
            if expected_count != len(ordered_nodes):
                raise ValueError(
                    f"SID block for routing domain {block.domain_id!r} has {expected_count} index(es) "
                    f"for {len(ordered_nodes)} node(s)"
                )
            result[block.domain_id] = {
                node_id: block.sid_start + offset for offset, node_id in enumerate(ordered_nodes)
            }
        return result

    def link_interface_map(self) -> dict[tuple[str, str], tuple[str, str]]:
        """Return resolver-assigned fixed-link interfaces by canonical pair.

        A candidate whose terminals OME assigns per pass never appears in this map.
        """
        return {
            candidate.pair: candidate.fixed_interfaces
            for candidate in self.link_candidates
            if not candidate.assigned_terminals
        }

    def long_delay_fixed_pairs(self) -> frozenset[tuple[str, str]]:
        """The fixed links whose class is long-delay, by canonical pair. OME names each contact
        on them and the Scheduler drains each at its end. A long-delay candidate whose
        terminals are assigned per pass is not among them: its terminals are chosen per
        contact."""
        return frozenset(
            candidate.pair
            for candidate in self.link_candidates
            if not candidate.assigned_terminals and candidate.delay_class == "long_delay"
        )

    def interface_terminal_rates(self) -> dict[tuple[str, str], InterfaceRates]:
        """Each WAN interface's own terminal rates, keyed by (node_id, interface).

        Link shaping applies them per interface: egress at the terminal's
        transmit rate and ingress at its receive rate.
        """
        rates: dict[tuple[str, str], InterfaceRates] = {}
        for node in self.nodes:
            for wan in node.wan_interfaces:
                block = node.wan_terminal(wan.name)
                rates[(node.node_id, wan.name)] = InterfaceRates(
                    block.transmit_mbps, block.receive_mbps
                )
        return rates

    def wan_interface_peers(self) -> dict[tuple[str, str], frozenset[str]]:
        """The nodes each WAN interface can reach, keyed by (node_id, interface).

        A fixed link's interface reaches its one peer. An interface assigned per pass
        reaches the peer of every candidate it can carry (``candidate_interfaces``). An
        assigned interface that can carry none has an empty entry.
        """
        peers: dict[tuple[str, str], set[str]] = {
            (node_id, interface): set()
            for node_id, interfaces in self.assigned_interfaces_by_node().items()
            for interface in interfaces
        }
        carried = self.candidate_interfaces()
        for candidate in self.link_candidates:
            names_a, names_b = carried[candidate.pair]
            for name in names_a:
                peers.setdefault((candidate.node_a, name), set()).add(candidate.node_b)
            for name in names_b:
                peers.setdefault((candidate.node_b, name), set()).add(candidate.node_a)
        return {key: frozenset(value) for key, value in peers.items()}

    def ground_candidate_satellites_by_gs(self) -> dict[str, tuple[str, ...]]:
        """Return access candidate satellites keyed by ground station node id."""
        ground_ids = {node.node_id for node in self.nodes if node.placement_frame == "surface"}
        satellite_ids = {node.node_id for node in self.nodes if node.placement_frame == "orbit"}
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
