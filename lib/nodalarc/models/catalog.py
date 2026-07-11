# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed catalog primitive models."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    WithJsonSchema,
    model_validator,
)

from nodalarc.catalog_refs import BodyRef, NodeRef, OrbitRef, PayloadRef, SiteRef, TerminalRef
from nodalarc.model_validation import (
    AwareTimestamp,
    FiniteFloat,
    Identifier,
    Ipv4Interface,
    Ipv4Network,
    Ipv6Interface,
    Ipv6Network,
    NonNegativeFiniteFloat,
    NonNegativeInteger,
    PositiveFiniteFloat,
    PositiveInteger,
    TerminalMedium,
)
from nodalarc.models.segments import (
    GroundScheduling,
    LagrangeFrame,
    OriginatedPrefixes,
    SegmentClock,
)
from nodalarc.tle import validate_tle_pair

_URL_ADAPTER = TypeAdapter(AnyUrl)


def _validate_url(value: str) -> str:
    _URL_ADAPTER.validate_python(value)
    return value


Url = Annotated[
    str,
    AfterValidator(_validate_url),
    WithJsonSchema({"type": "string", "format": "uri"}),
]

MountRole = Literal["access", "isl", "crosslink", "backbone"]
ForwardingClass = Literal["routed", "host", "bridge", "control_only"]
# "crtbp" (three-body NRHO/halo trajectories) is structurally valid grammar;
# the runtime-support layer rejects it with a typed UnsupportedFeature until a
# CR3BP propagator lands. Kepler elements cannot represent those orbits.
Propagator = Literal["two_body", "j2_mean_elements", "crtbp"]
PhasingMode = Literal["walker_delta", "walker_star", "evenly_spaced_mean_anomaly"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def _unique_tags(self) -> _FrozenModel:
        tags = getattr(self, "tags", None)
        if tags is not None and len(set(tags)) != len(tags):
            raise ValueError("tags must not contain duplicates")
        return self


class Body(_FrozenModel):
    id: Identifier
    display_name: str
    gravitational_parameter_km3_s2: PositiveFiniteFloat
    mean_radius_km: PositiveFiniteFloat
    equatorial_radius_km: PositiveFiniteFloat
    polar_radius_km: PositiveFiniteFloat
    reference: Url
    notes: str | None = None


class DirectionalBandwidth(_FrozenModel):
    transmit: PositiveFiniteFloat
    receive: PositiveFiniteFloat


class RfSignal(_FrozenModel):
    band: Identifier
    frequency_hz: PositiveFiniteFloat


class OpticalSignal(_FrozenModel):
    wavelength_nm: PositiveFiniteFloat


class AngleRange(_FrozenModel):
    min: FiniteFloat
    max: FiniteFloat

    @model_validator(mode="after")
    def _ordered(self) -> AngleRange:
        if self.max < self.min:
            raise ValueError("angle range max must be >= min")
        return self


class TerminalLimits(_FrozenModel):
    azimuth_deg: AngleRange
    elevation_deg: AngleRange
    max_tracking_rate_deg_s: PositiveFiniteFloat


class LocalVerticalBoresight(_FrozenModel):
    mode: Literal["local_vertical"]


class ConfiguredTopocentricBoresight(_FrozenModel):
    mode: Literal["configured_topocentric"]
    azimuth_deg: FiniteFloat
    elevation_deg: FiniteFloat


class SteerableEnvelopeBoresight(_FrozenModel):
    mode: Literal["steerable_envelope"]
    azimuth_deg: AngleRange
    elevation_deg: AngleRange


Boresight = LocalVerticalBoresight | ConfiguredTopocentricBoresight | SteerableEnvelopeBoresight


class NadirBoresight(_FrozenModel):
    mode: Literal["nadir"]


class Terminal(_FrozenModel):
    id: Identifier
    display_name: str
    medium: TerminalMedium
    signal: RfSignal | OpticalSignal
    bandwidth_mbps: DirectionalBandwidth
    tracking_capacity: PositiveInteger
    max_range_km: PositiveFiniteFloat
    limits: TerminalLimits
    reference: Url
    notes: str | None = None

    @model_validator(mode="after")
    def _signal_matches_medium(self) -> Terminal:
        if self.medium == "rf" and not isinstance(self.signal, RfSignal):
            raise ValueError("rf terminal requires rf signal fields")
        if self.medium == "optical" and not isinstance(self.signal, OpticalSignal):
            raise ValueError("optical terminal requires optical signal fields")
        return self


class OrbitElements(_FrozenModel):
    semi_major_axis_km: PositiveFiniteFloat
    eccentricity: Annotated[FiniteFloat, Field(ge=0, lt=1)]


class CircularShape(_FrozenModel):
    altitude_km: PositiveFiniteFloat


class PerigeeApogeeShape(_FrozenModel):
    perigee_altitude_km: PositiveFiniteFloat
    apogee_altitude_km: PositiveFiniteFloat

    @model_validator(mode="after")
    def _ordered(self) -> PerigeeApogeeShape:
        if self.apogee_altitude_km < self.perigee_altitude_km:
            raise ValueError("apogee_altitude_km must be >= perigee_altitude_km")
        return self


OrbitShape = CircularShape | PerigeeApogeeShape


class OrbitOrientation(_FrozenModel):
    inclination_deg: FiniteFloat
    raan_deg: FiniteFloat
    argument_of_perigee_deg: FiniteFloat


class OrbitPhase(_FrozenModel):
    mean_anomaly_deg: FiniteFloat


class Orbit(_FrozenModel):
    id: Identifier
    central_body: BodyRef
    epoch: AwareTimestamp
    elements: OrbitElements | None = None
    shape: OrbitShape | None = None
    orientation: OrbitOrientation
    phase: OrbitPhase
    propagator: Propagator
    reference: Url
    notes: str | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Orbit:
        if (self.elements is None) == (self.shape is None):
            raise ValueError("orbit requires exactly one of elements or shape")
        return self


class TerminalSlot(_FrozenModel):
    id: Identifier
    terminal: TerminalRef
    tags: tuple[Identifier, ...] | None = None


class PayloadResourceGroup(_FrozenModel):
    id: Identifier
    slots: tuple[Identifier, ...] = Field(min_length=1)
    simultaneous_active: PositiveInteger


class Payload(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    terminal_slots: tuple[TerminalSlot, ...] = Field(min_length=1)
    resource_groups: tuple[PayloadResourceGroup, ...] = ()
    reference: Url | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _valid_resource_groups(self) -> Payload:
        slot_ids = [slot.id for slot in self.terminal_slots]
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("payload terminal slot ids must be unique")

        group_ids = [group.id for group in self.resource_groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("payload resource group ids must be unique")

        declared_slots = set(slot_ids)
        for group in self.resource_groups:
            if len(set(group.slots)) != len(group.slots):
                raise ValueError(f"payload resource group {group.id!r} slots must be unique")
            unknown_slots = sorted(set(group.slots) - declared_slots)
            if unknown_slots:
                raise ValueError(
                    f"payload resource group {group.id!r} references unknown terminal "
                    f"slot(s): {unknown_slots}"
                )
            if group.simultaneous_active > len(group.slots):
                raise ValueError(
                    f"payload resource group {group.id!r} simultaneous_active must not "
                    "exceed its slot count"
                )
        return self


class EthernetPort(_FrozenModel):
    id: Identifier
    tags: tuple[Identifier, ...] | None = None


class TerminalMount(_FrozenModel):
    id: Identifier
    role: MountRole
    terminal: TerminalRef
    count: PositiveInteger
    boresight: NadirBoresight | None = None
    tags: tuple[Identifier, ...] | None = None


class PayloadMount(_FrozenModel):
    id: Identifier
    payload: PayloadRef
    count: PositiveInteger
    tags: tuple[Identifier, ...] | None = None


class Node(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    forwarding: ForwardingClass
    ethernet: tuple[EthernetPort, ...]
    terminals: tuple[TerminalMount, ...]
    payloads: tuple[PayloadMount, ...]
    tags: tuple[Identifier, ...] | None = None
    reference: Url | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _unique_component_ids(self) -> Node:
        ethernet_ids = [port.id for port in self.ethernet]
        if len(set(ethernet_ids)) != len(ethernet_ids):
            raise ValueError("node ethernet port ids must be unique")
        terminal_ids = [mount.id for mount in self.terminals]
        if len(set(terminal_ids)) != len(terminal_ids):
            raise ValueError("node terminal mount ids must be unique")
        payload_ids = [mount.id for mount in self.payloads]
        if len(set(payload_ids)) != len(payload_ids):
            raise ValueError("node payload mount ids must be unique")
        invalid_boresights = [
            mount.id
            for mount in self.terminals
            if mount.boresight is not None and mount.role != "access"
        ]
        if invalid_boresights:
            raise ValueError(
                f"node boresight is valid only on access terminal mounts: {invalid_boresights}"
            )
        return self


class VerificationMetadata(_FrozenModel):
    source: str
    filing: str | None = None
    reference: Url | None = None
    confidence: Identifier | None = None
    notes: str | None = None


class SiteLan(_FrozenModel):
    ipv4: Ipv4Network | None = None
    ipv6: Ipv6Network | None = None

    @model_validator(mode="after")
    def _has_address_family(self) -> SiteLan:
        if self.ipv4 is None and self.ipv6 is None:
            raise ValueError("site lan requires ipv4 and/or ipv6")
        return self


class BodyFixedFrame(_FrozenModel):
    body: BodyRef


class BodyFixedFrameWrapper(_FrozenModel):
    body_fixed: BodyFixedFrame


class EphemerisAnchorFrame(_FrozenModel):
    frame: Identifier


class EphemerisAnchorFrameWrapper(_FrozenModel):
    ephemeris_anchor: EphemerisAnchorFrame


class SiteLocation(_FrozenModel):
    lat_deg: Annotated[FiniteFloat, Field(ge=-90, le=90)]
    lon_deg: Annotated[FiniteFloat, Field(ge=-180, le=180)]
    alt_m: FiniteFloat


class InterfaceAddress(_FrozenModel):
    ipv4: Ipv4Interface | None = None
    ipv6: Ipv6Interface | None = None

    @model_validator(mode="after")
    def _has_address_family(self) -> InterfaceAddress:
        if self.ipv4 is None and self.ipv6 is None:
            raise ValueError("interface requires ipv4 and/or ipv6")
        return self


class NodeInterfaces(_FrozenModel):
    lo0: InterfaceAddress
    terr0: InterfaceAddress


class PayloadInstallation(_FrozenModel):
    installed_count: PositiveInteger
    tags: tuple[Identifier, ...] | None = None


class TerminalCapabilities(_FrozenModel):
    bandwidth_mbps: DirectionalBandwidth | None = None
    tracking_capacity: PositiveInteger | None = None
    max_range_km: PositiveFiniteFloat | None = None
    limits: TerminalLimits | None = None
    boresight: Boresight | None = None


class TerminalInstallation(_FrozenModel):
    installed_count: PositiveInteger
    capabilities: TerminalCapabilities | None = None
    tags: tuple[Identifier, ...] | None = None


class SiteNode(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    model: NodeRef
    terminals: dict[Identifier, TerminalInstallation]
    payloads: dict[Identifier, PayloadInstallation]
    interfaces: NodeInterfaces
    originated_prefixes: OriginatedPrefixes | None = None
    tenant_id: Identifier | None = None
    service_priority: PositiveInteger | None = None
    scheduling: GroundScheduling | None = None
    tags: tuple[Identifier, ...] | None = None


SiteFrame = BodyFixedFrameWrapper | LagrangeFrame | EphemerisAnchorFrameWrapper


class Site(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    verified: VerificationMetadata | None = None
    lan: SiteLan
    tags: tuple[Identifier, ...] | None = None
    nodes: tuple[SiteNode, ...] = Field(min_length=1)
    frame: SiteFrame
    location: SiteLocation | None = None

    @model_validator(mode="after")
    def _location_matches_frame(self) -> Site:
        if isinstance(self.frame, BodyFixedFrameWrapper) and self.location is None:
            raise ValueError("body_fixed site requires location")
        if not isinstance(self.frame, BodyFixedFrameWrapper) and self.location is not None:
            raise ValueError("non-body-fixed site must not set location")
        return self

    @model_validator(mode="after")
    def _valid_node_addresses(self) -> Site:
        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("site node ids must be unique")

        seen_addresses: dict[tuple[int, str], str] = {}
        for node in self.nodes:
            for interface_name in ("lo0", "terr0"):
                interface = getattr(node.interfaces, interface_name)
                for family in ("ipv4", "ipv6"):
                    address = getattr(interface, family)
                    if address is None:
                        continue
                    parsed = ipaddress.ip_interface(address)
                    address_key = (parsed.version, str(parsed.ip))
                    owner = f"{node.id}.{interface_name}.{family}"
                    existing_owner = seen_addresses.get(address_key)
                    if existing_owner is not None:
                        raise ValueError(
                            f"site interface address {parsed.ip} is installed more than once: "
                            f"{existing_owner}, {owner}"
                        )
                    seen_addresses[address_key] = owner

                    if interface_name != "terr0":
                        continue
                    lan = getattr(self.lan, family)
                    if lan is None:
                        raise ValueError(
                            f"site node {node.id!r} terr0 declares {family} but site lan "
                            f"does not declare {family}"
                        )
                    if parsed.ip not in ipaddress.ip_network(lan):
                        raise ValueError(
                            f"site node {node.id!r} terr0 {family} address {parsed.ip} "
                            f"is outside site lan {lan}"
                        )
        return self


class SiteSet(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    sites: tuple[SiteRef, ...] = Field(min_length=1)
    tags: tuple[Identifier, ...] | None = None
    reference: Url | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _unique_sites(self) -> SiteSet:
        if len(set(self.sites)) != len(self.sites):
            raise ValueError("site_set sites must not contain duplicates")
        return self


class PlaneParams(_FrozenModel):
    count: PositiveInteger
    raan_spacing_deg: NonNegativeFiniteFloat


class Phasing(_FrozenModel):
    mode: PhasingMode
    phase_offset_deg: FiniteFloat | None = None


class NodeTagRule(_FrozenModel):
    tag: Identifier
    planes: tuple[NonNegativeInteger, ...] | None = None
    slots: tuple[NonNegativeInteger, ...] | None = None
    node_ids: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def _valid_rule(self) -> NodeTagRule:
        if self.node_ids is not None and (self.planes is not None or self.slots is not None):
            raise ValueError("node tag rule cannot mix node_ids with plane/slot selectors")
        for field_name in ("planes", "slots", "node_ids"):
            values = getattr(self, field_name)
            if values is None:
                continue
            if not values:
                raise ValueError(f"node tag rule {field_name} must not be empty")
            if len(set(values)) != len(values):
                raise ValueError(f"node tag rule {field_name} must not contain duplicates")
        if self.planes is not None and any(plane < 0 for plane in self.planes):
            raise ValueError("node tag rule planes must be non-negative")
        if self.slots is not None and any(slot < 0 for slot in self.slots):
            raise ValueError("node tag rule slots must be non-negative")
        return self


class Constellation(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    node: NodeRef
    orbit: OrbitRef
    planes: PlaneParams
    slots_per_plane: PositiveInteger
    phasing: Phasing
    node_tags: tuple[NodeTagRule, ...]
    tags: tuple[Identifier, ...] | None = None
    reference: Url | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _phasing_matches_population(self) -> Constellation:
        if self.phasing.mode == "evenly_spaced_mean_anomaly":
            if self.planes.count != 1:
                raise ValueError(
                    "evenly_spaced_mean_anomaly phasing requires exactly one orbital plane"
                )
            if self.phasing.phase_offset_deg not in (None, 0.0):
                raise ValueError(
                    "single-plane evenly_spaced_mean_anomaly phasing must not declare a "
                    "non-zero inter-plane phase_offset_deg"
                )
        else:
            if self.planes.count < 2:
                raise ValueError(f"{self.phasing.mode} phasing requires at least two planes")
            if self.phasing.phase_offset_deg is None:
                raise ValueError(f"{self.phasing.mode} phasing requires phase_offset_deg")
        return self

    @model_validator(mode="after")
    def _tag_filters_exist(self) -> Constellation:
        for rule in self.node_tags:
            if rule.planes is not None and any(plane >= self.planes.count for plane in rule.planes):
                raise ValueError(
                    f"node tag rule {rule.tag!r} references a plane outside this constellation"
                )
            if rule.slots is not None and any(slot >= self.slots_per_plane for slot in rule.slots):
                raise ValueError(
                    f"node tag rule {rule.tag!r} references a slot outside this constellation"
                )
            for node_id in rule.node_ids or ():
                match = re.fullmatch(r"sat-p([0-9]+)s([0-9]+)", node_id)
                if match is None:
                    raise ValueError(
                        f"node tag rule {rule.tag!r} references unknown generated node {node_id!r}"
                    )
                plane = int(match.group(1))
                slot = int(match.group(2))
                expected = f"sat-p{plane:02d}s{slot:02d}"
                if (
                    node_id != expected
                    or plane >= self.planes.count
                    or slot >= self.slots_per_plane
                ):
                    raise ValueError(
                        f"node tag rule {rule.tag!r} references unknown generated node {node_id!r}"
                    )
        return self


class StateVector(_FrozenModel):
    epoch: AwareTimestamp
    frame: Identifier
    position_km: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    velocity_km_s: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class Sgp4TlePlacement(_FrozenModel):
    central_body: BodyRef
    line_1: str = Field(min_length=1)
    line_2: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_pair(self) -> Sgp4TlePlacement:
        validate_tle_pair(self.line_1, self.line_2)
        return self


class SpaceNode(_FrozenModel):
    id: Identifier
    node: NodeRef
    orbit: OrbitRef | None = None
    sgp4_tle: Sgp4TlePlacement | None = None
    state_vector: StateVector | None = None
    tags: tuple[Identifier, ...] | None = None
    clock: SegmentClock | None = None

    @model_validator(mode="after")
    def _placement(self) -> SpaceNode:
        placements = (self.orbit, self.sgp4_tle, self.state_vector)
        if sum(value is not None for value in placements) != 1:
            raise ValueError("space_node requires exactly one of orbit, sgp4_tle, or state_vector")
        return self


class SpaceNodeSet(_FrozenModel):
    id: Identifier
    nodes: tuple[SpaceNode, ...] = Field(min_length=1)
    tags: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def _unique_node_ids(self) -> SpaceNodeSet:
        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("space_node_set node ids must be unique")
        return self


class _CatalogDocumentRoot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BodyDocument(_CatalogDocumentRoot):
    body: Body


class TerminalDocument(_CatalogDocumentRoot):
    terminal: Terminal


class PayloadDocument(_CatalogDocumentRoot):
    payload: Payload


class OrbitDocument(_CatalogDocumentRoot):
    orbit: Orbit


class NodeDocument(_CatalogDocumentRoot):
    node: Node


class SiteDocument(_CatalogDocumentRoot):
    site: Site


class SiteSetDocument(_CatalogDocumentRoot):
    site_set: SiteSet


class ConstellationDocument(_CatalogDocumentRoot):
    constellation: Constellation


class SpaceNodeSetDocument(_CatalogDocumentRoot):
    space_node_set: SpaceNodeSet
