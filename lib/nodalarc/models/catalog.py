# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed catalog primitive models.

The catalog grammar is object-level: a primitive has one schema whether it is
loaded from a file or provided inline. References are loader syntax around these
models, not alternate schemas.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, model_validator

from nodalarc.models.segments import GroundScheduling, OriginatedPrefixes

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
CatalogObject = Annotated[str, Field(min_length=1)] | dict[str, Any]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]

TerminalMedium = Literal["rf", "optical"]
MountRole = Literal["access", "isl", "crosslink", "backbone"]
ForwardingClass = Literal["routed", "host", "bridge", "control_only"]
# "crtbp" (three-body NRHO/halo trajectories) is structurally valid grammar;
# the runtime-support layer rejects it with a typed UnsupportedFeature until a
# CR3BP propagator lands. Kepler elements cannot represent those orbits.
Propagator = Literal["two_body", "j2_mean_elements", "sgp4_tle", "crtbp"]
PhasingMode = Literal["walker_delta", "walker_star", "evenly_spaced_mean_anomaly"]
BoresightMode = Literal["local_vertical", "configured_topocentric", "steerable_envelope"]
LagrangePoint = Literal["l1", "l2", "l3", "l4", "l5"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Body(_FrozenModel):
    id: Identifier
    display_name: str
    gravitational_parameter_km3_s2: PositiveFloat
    mean_radius_km: PositiveFloat
    equatorial_radius_km: PositiveFloat
    polar_radius_km: PositiveFloat
    reference: str
    notes: str | None = None


class DirectionalBandwidth(_FrozenModel):
    transmit: NonNegativeFiniteFloat
    receive: NonNegativeFiniteFloat


class RfSignal(_FrozenModel):
    band: Identifier
    frequency_hz: PositiveFloat


class OpticalSignal(_FrozenModel):
    wavelength_nm: PositiveFloat


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
    max_tracking_rate_deg_s: PositiveFloat


class Terminal(_FrozenModel):
    id: Identifier
    display_name: str
    medium: TerminalMedium
    signal: RfSignal | OpticalSignal
    bandwidth_mbps: DirectionalBandwidth
    tracking_capacity: PositiveInt
    max_range_km: PositiveFloat
    limits: TerminalLimits
    reference: str
    notes: str | None = None

    @model_validator(mode="after")
    def _signal_matches_medium(self) -> Terminal:
        if self.medium == "rf" and not isinstance(self.signal, RfSignal):
            raise ValueError("rf terminal requires rf signal fields")
        if self.medium == "optical" and not isinstance(self.signal, OpticalSignal):
            raise ValueError("optical terminal requires optical signal fields")
        return self


class OrbitElements(_FrozenModel):
    semi_major_axis_km: PositiveFloat
    eccentricity: NonNegativeFiniteFloat


class CircularShape(_FrozenModel):
    altitude_km: PositiveFloat


class PerigeeApogeeShape(_FrozenModel):
    perigee_altitude_km: PositiveFloat
    apogee_altitude_km: PositiveFloat

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
    central_body: CatalogObject
    epoch: str
    elements: OrbitElements | None = None
    shape: OrbitShape | None = None
    orientation: OrbitOrientation
    phase: OrbitPhase
    propagator: Propagator
    reference: str
    notes: str | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Orbit:
        if (self.elements is None) == (self.shape is None):
            raise ValueError("orbit requires exactly one of elements or shape")
        return self


class TerminalSlot(_FrozenModel):
    id: Identifier
    terminal: CatalogObject
    tags: tuple[Identifier, ...] | None = None


class PayloadResourceGroup(_FrozenModel):
    id: Identifier
    slots: tuple[Identifier, ...] = Field(min_length=1)
    simultaneous_active: PositiveInt


class Payload(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    terminal_slots: tuple[TerminalSlot, ...] = Field(min_length=1)
    resource_groups: tuple[PayloadResourceGroup, ...] = ()
    reference: str | None = None
    notes: str | None = None


class EthernetPort(_FrozenModel):
    id: Identifier
    tags: tuple[Identifier, ...] | None = None


class TerminalMount(_FrozenModel):
    id: Identifier
    role: MountRole
    terminal: CatalogObject
    count: PositiveInt
    tags: tuple[Identifier, ...] | None = None


class PayloadMount(_FrozenModel):
    id: Identifier
    payload: CatalogObject
    count: PositiveInt
    tags: tuple[Identifier, ...] | None = None


class Node(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    forwarding: ForwardingClass
    ethernet: tuple[EthernetPort, ...]
    terminals: tuple[TerminalMount, ...]
    payloads: tuple[PayloadMount, ...]
    tags: tuple[Identifier, ...] | None = None
    reference: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _unique_mount_ids(self) -> Node:
        terminal_ids = [mount.id for mount in self.terminals]
        if len(set(terminal_ids)) != len(terminal_ids):
            raise ValueError("node terminal mount ids must be unique")
        payload_ids = [mount.id for mount in self.payloads]
        if len(set(payload_ids)) != len(payload_ids):
            raise ValueError("node payload mount ids must be unique")
        return self


class VerificationMetadata(_FrozenModel):
    source: str
    filing: str | None = None
    reference: str | None = None
    confidence: Identifier | None = None
    notes: str | None = None


class SiteLan(_FrozenModel):
    ipv4: str | None = None
    ipv6: str | None = None

    @model_validator(mode="after")
    def _has_address_family(self) -> SiteLan:
        if self.ipv4 is None and self.ipv6 is None:
            raise ValueError("site lan requires ipv4 and/or ipv6")
        return self


class BodyFixedFrame(_FrozenModel):
    body: CatalogObject


class BodyFixedFrameWrapper(_FrozenModel):
    body_fixed: BodyFixedFrame


class EphemerisAnchorFrame(_FrozenModel):
    frame: Identifier


class EphemerisAnchorFrameWrapper(_FrozenModel):
    ephemeris_anchor: EphemerisAnchorFrame


class ConfiguredStateLagrange(_FrozenModel):
    configured_state: dict[str, Any]


class ApproximateLagrange(_FrozenModel):
    lagrange_approximation: dict[str, Any] = Field(default_factory=dict)


class ExternalEphemerisLagrange(_FrozenModel):
    external_ephemeris: dict[str, str]


class LagrangeFrameBody(_FrozenModel):
    primary_body: CatalogObject
    secondary_body: CatalogObject
    point: LagrangePoint
    ephemeris: ConfiguredStateLagrange | ApproximateLagrange | ExternalEphemerisLagrange


class LagrangeFrameWrapper(_FrozenModel):
    lagrange: LagrangeFrameBody


class SiteLocation(_FrozenModel):
    lat_deg: FiniteFloat
    lon_deg: FiniteFloat
    alt_m: FiniteFloat


class InterfaceAddress(_FrozenModel):
    ipv4: str | None = None
    ipv6: str | None = None

    @model_validator(mode="after")
    def _has_address_family(self) -> InterfaceAddress:
        if self.ipv4 is None and self.ipv6 is None:
            raise ValueError("interface requires ipv4 and/or ipv6")
        return self


class NodeInterfaces(_FrozenModel):
    lo0: InterfaceAddress
    terr0: InterfaceAddress


class PayloadInstallation(_FrozenModel):
    installed_count: PositiveInt
    tags: tuple[Identifier, ...] | None = None


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


class TerminalCapabilities(_FrozenModel):
    bandwidth_mbps: DirectionalBandwidth | None = None
    tracking_capacity: PositiveInt | None = None
    max_range_km: PositiveFloat | None = None
    limits: TerminalLimits | None = None
    boresight: Boresight | None = None


class TerminalInstallation(_FrozenModel):
    installed_count: PositiveInt
    capabilities: TerminalCapabilities | None = None
    tags: tuple[Identifier, ...] | None = None


class SiteNode(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    model: CatalogObject
    terminals: dict[Identifier, TerminalInstallation]
    payloads: dict[Identifier, PayloadInstallation]
    interfaces: NodeInterfaces
    originated_prefixes: OriginatedPrefixes | None = None
    tenant_id: Identifier | None = None
    service_priority: PositiveInt | None = None
    scheduling: GroundScheduling | None = None
    tags: tuple[Identifier, ...] | None = None


SiteFrame = BodyFixedFrameWrapper | LagrangeFrameWrapper | EphemerisAnchorFrameWrapper


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


class SiteSet(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    sites: tuple[CatalogObject, ...] = Field(min_length=1)
    tags: tuple[Identifier, ...] | None = None
    reference: str | None = None
    notes: str | None = None


class PlaneParams(_FrozenModel):
    count: PositiveInt
    raan_spacing_deg: NonNegativeFiniteFloat


class Phasing(_FrozenModel):
    mode: PhasingMode
    phase_offset_deg: FiniteFloat | None = None


class NodeTagRule(_FrozenModel):
    tag: Identifier
    planes: tuple[int, ...] | None = None
    slots: tuple[int, ...] | None = None
    node_ids: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def _valid_rule(self) -> NodeTagRule:
        if self.node_ids is not None and (self.planes is not None or self.slots is not None):
            raise ValueError("node tag rule cannot mix node_ids with plane/slot selectors")
        if self.planes is not None and any(plane < 0 for plane in self.planes):
            raise ValueError("node tag rule planes must be non-negative")
        if self.slots is not None and any(slot < 0 for slot in self.slots):
            raise ValueError("node tag rule slots must be non-negative")
        return self


class Constellation(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    node: CatalogObject
    orbit: CatalogObject
    planes: PlaneParams
    slots_per_plane: PositiveInt
    phasing: Phasing
    node_tags: tuple[NodeTagRule, ...]
    tags: tuple[Identifier, ...] | None = None
    reference: str | None = None
    notes: str | None = None


class StateVector(_FrozenModel):
    epoch: str
    frame: Identifier
    position_km: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    velocity_km_s: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class SpaceNode(_FrozenModel):
    id: Identifier
    node: CatalogObject
    orbit: CatalogObject | None = None
    state_vector: StateVector | None = None
    tags: tuple[Identifier, ...] | None = None
    clock: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _placement(self) -> SpaceNode:
        if (self.orbit is None) == (self.state_vector is None):
            raise ValueError("space_node requires exactly one of orbit or state_vector")
        return self


class SpaceNodeSet(_FrozenModel):
    id: Identifier
    nodes: tuple[SpaceNode, ...] = Field(min_length=1)
    tags: tuple[Identifier, ...] | None = None


WRAPPER_MODELS: dict[str, type[BaseModel]] = {
    "body": Body,
    "terminal": Terminal,
    "orbit": Orbit,
    "payload": Payload,
    "node": Node,
    "site": Site,
    "site_set": SiteSet,
    "constellation": Constellation,
    "space_node": SpaceNode,
    "space_node_set": SpaceNodeSet,
}


def validate_catalog_document(data: Any) -> tuple[str, BaseModel]:
    """Validate one catalog document and return its wrapper name and model."""

    if not isinstance(data, dict):
        raise ValueError("catalog document must be a mapping")
    if len(data) != 1:
        raise ValueError("catalog document must contain exactly one top-level object wrapper")
    wrapper = next(iter(data))
    model_type = WRAPPER_MODELS.get(wrapper)
    if model_type is None:
        raise ValueError(f"unsupported catalog object wrapper {wrapper!r}")
    value = data[wrapper]
    return wrapper, validate_catalog_value(wrapper, value)


def validate_catalog_value(wrapper: str, value: Any) -> BaseModel:
    """Validate an unwrapped primitive value for a known catalog wrapper."""

    model_type = WRAPPER_MODELS.get(wrapper)
    if model_type is None:
        raise ValueError(f"unsupported catalog object wrapper {wrapper!r}")
    if not isinstance(value, dict):
        raise ValueError(f"catalog object {wrapper!r} must be a mapping")
    return model_type.model_validate(value)
