# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed catalog primitive models."""

from __future__ import annotations

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

from nodalarc.catalog_refs import (
    BodyRef,
    NodeRef,
    OrbitRef,
    PayloadRef,
    ProfileRef,
    SiteRef,
    TerminalRef,
)
from nodalarc.model_validation import (
    AwareTimestamp,
    EnvName,
    FiniteFloat,
    Identifier,
    MountPath,
    NonNegativeFiniteFloat,
    NonNegativeInteger,
    PinnedImage,
    PositiveFiniteFloat,
    PositiveInteger,
    RegistryHost,
    SegmentId,
    StrictBoolean,
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


class Payload(_FrozenModel):
    """A carried compute environment: what it is and what it runs.

    A payload declares no Ethernet ports, no terminal mounts, and no payload
    mounts, so payload composition cannot recurse. Where it attaches is its
    mount's decision.
    """

    id: Identifier
    display_name: str | None = None
    forwarding: Literal["routed", "host"]
    profile: ProfileRef
    tags: tuple[Identifier, ...] | None = None
    reference: Url | None = None
    notes: str | None = None


LinuxCapability = Literal[
    "AUDIT_WRITE",
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "FSETID",
    "KILL",
    "MKNOD",
    "NET_ADMIN",
    "NET_BIND_SERVICE",
    "NET_RAW",
    "SETFCAP",
    "SETGID",
    "SETPCAP",
    "SETUID",
    "SYS_ADMIN",
    "SYS_CHROOT",
]
RootFilesystem = Literal["read_only", "ephemeral_writable"]

_ARGV_MAX_ELEMENTS = 64
_ARGV_MAX_TOTAL_BYTES = 4096


def _validate_argv(argv: tuple[str, ...], *, field: str) -> None:
    if not argv:
        raise ValueError(f"{field} must be a nonempty list")
    if any(not element or "\x00" in element for element in argv):
        raise ValueError(f"{field} elements must be nonempty and NUL-free")
    if len(argv) > _ARGV_MAX_ELEMENTS:
        raise ValueError(f"{field} exceeds {_ARGV_MAX_ELEMENTS} elements")
    if sum(len(element.encode()) for element in argv) > _ARGV_MAX_TOTAL_BYTES:
        raise ValueError(f"{field} exceeds {_ARGV_MAX_TOTAL_BYTES} bytes total")


def _validate_capabilities(capabilities: tuple[str, ...]) -> None:
    if list(capabilities) != sorted(set(capabilities)):
        raise ValueError("capabilities must be unique and in ascending order")


def _mount_paths_conflict(path_a: str, path_b: str) -> bool:
    return path_a == path_b or path_a.startswith(f"{path_b}/") or path_b.startswith(f"{path_a}/")


def _validate_mount_conflicts(paths: list[str], *, owner: str) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if _mount_paths_conflict(path, other):
                raise ValueError(f"{owner} mount destinations conflict: {path!r} and {other!r}")


class ProfileVolume(_FrozenModel):
    name: Identifier
    kind: Literal["ephemeral"]
    medium: Literal["memory", "node"]
    size_mi: PositiveInteger


class ProfileMount(_FrozenModel):
    volume: Identifier
    path: MountPath
    read_only: StrictBoolean = False


class ProfileResourceAmounts(_FrozenModel):
    cpu_m: PositiveInteger
    memory_mi: PositiveInteger


class ProfileResources(_FrozenModel):
    requests: ProfileResourceAmounts
    limits: ProfileResourceAmounts

    @model_validator(mode="after")
    def _limits_cover_requests(self) -> ProfileResources:
        if self.limits.cpu_m < self.requests.cpu_m:
            raise ValueError("cpu limit must be >= cpu request")
        if self.limits.memory_mi < self.requests.memory_mi:
            raise ValueError("memory limit must be >= memory request")
        return self


class EnvValueFrom(_FrozenModel):
    """One resolved fact: the address of the single node carrying the tag."""

    tag: Identifier
    interface: Identifier
    family: Literal["ipv4", "ipv6"]


class LiteralEnvEntry(_FrozenModel):
    name: EnvName
    value: str


class ResolvedEnvEntry(_FrozenModel):
    name: EnvName
    value_from: EnvValueFrom


EnvEntry = LiteralEnvEntry | ResolvedEnvEntry


def _validate_env_names(entries: tuple[EnvEntry, ...], *, owner: str) -> None:
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ValueError(f"{owner} env names must be unique")


class SshTerminalSurface(_FrozenModel):
    surface: Literal["ssh"]
    authorized_keys_path: MountPath


class ExecTerminalSurface(_FrozenModel):
    surface: Literal["exec"]
    command: tuple[str, ...]

    @model_validator(mode="after")
    def _argv_rules(self) -> ExecTerminalSurface:
        _validate_argv(self.command, field="terminal command")
        return self


ProfileTerminal = SshTerminalSurface | ExecTerminalSurface


class ProfileReadiness(_FrozenModel):
    argv: tuple[str, ...]
    timeout_seconds: PositiveInteger
    period_seconds: PositiveInteger

    @model_validator(mode="after")
    def _argv_rules(self) -> ProfileReadiness:
        _validate_argv(self.argv, field="readiness argv")
        return self


class ProfileSidecar(_FrozenModel):
    name: Identifier
    registry: RegistryHost | None = None
    image: PinnedImage
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    env: tuple[EnvEntry, ...] = ()
    capabilities: tuple[LinuxCapability, ...] = ()
    root_filesystem: RootFilesystem = "read_only"
    resources: ProfileResources
    mounts: tuple[ProfileMount, ...] = ()

    @model_validator(mode="after")
    def _sidecar_rules(self) -> ProfileSidecar:
        _validate_capabilities(self.capabilities)
        for field_name in ("command", "args"):
            argv = getattr(self, field_name)
            if argv is not None:
                _validate_argv(argv, field=f"sidecar {field_name}")
        _validate_env_names(self.env, owner=f"sidecar {self.name!r}")
        _validate_mount_conflicts(
            [mount.path for mount in self.mounts], owner=f"sidecar {self.name!r}"
        )
        return self


class Profile(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    adapter: Identifier | None = None
    registry: RegistryHost
    image: PinnedImage
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    env: tuple[EnvEntry, ...] = ()
    capabilities: tuple[LinuxCapability, ...] = ()
    root_filesystem: RootFilesystem = "read_only"
    config_mount: MountPath | None = None
    volumes: tuple[ProfileVolume, ...] = ()
    mounts: tuple[ProfileMount, ...] = ()
    resources: ProfileResources
    readiness: ProfileReadiness | None = None
    terminal: ProfileTerminal | None = None
    sidecars: tuple[ProfileSidecar, ...] = ()
    reference: Url | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _profile_rules(self) -> Profile:
        _validate_capabilities(self.capabilities)
        for field_name in ("command", "args"):
            argv = getattr(self, field_name)
            if argv is not None:
                _validate_argv(argv, field=field_name)
        _validate_env_names(self.env, owner="profile")

        if self.config_mount is not None and self.adapter is None:
            raise ValueError("config_mount requires an adapter")

        volume_names = [volume.name for volume in self.volumes]
        if len(set(volume_names)) != len(volume_names):
            raise ValueError("profile volume names must be unique")
        declared_volumes = set(volume_names)
        for mount in self.mounts:
            if mount.volume not in declared_volumes:
                raise ValueError(f"profile mounts undeclared volume {mount.volume!r}")
        for sidecar in self.sidecars:
            for mount in sidecar.mounts:
                if mount.volume not in declared_volumes:
                    raise ValueError(
                        f"sidecar {sidecar.name!r} mounts undeclared volume {mount.volume!r}"
                    )

        primary_paths = [mount.path for mount in self.mounts]
        if self.config_mount is not None:
            primary_paths.append(self.config_mount)
        _validate_mount_conflicts(primary_paths, owner="profile")

        sidecar_names = [sidecar.name for sidecar in self.sidecars]
        if len(set(sidecar_names)) != len(sidecar_names):
            raise ValueError("sidecar names must be unique")
        if self.id in sidecar_names:
            raise ValueError("a sidecar must not use the profile id as its name")
        return self


class EthernetPort(_FrozenModel):
    id: SegmentId
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
    profile: ProfileRef | None = None
    count: PositiveInteger
    # The Ethernet port of the mounting node whose segment the mounted
    # environment joins; also the mounted environment's interface name.
    attach: SegmentId
    tags: tuple[Identifier, ...] | None = None


class Node(_FrozenModel):
    id: Identifier
    display_name: str | None = None
    forwarding: ForwardingClass
    profile: ProfileRef | None = None
    ethernet: tuple[EthernetPort, ...]
    terminals: tuple[TerminalMount, ...]
    payloads: tuple[PayloadMount, ...]
    # Symbolic routing-injection intent for the segments this node carries:
    # entries name the node's own declared Ethernet ports, or `default`.
    originated_prefixes: OriginatedPrefixes | None = None
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
        undeclared_attach = [
            mount.id for mount in self.payloads if mount.attach not in set(ethernet_ids)
        ]
        if undeclared_attach:
            raise ValueError(
                f"payload mount attach must name a declared ethernet port: {undeclared_attach}"
            )
        if self.originated_prefixes is not None:
            declared = {*ethernet_ids, "default"}
            unknown_targets = sorted(
                {
                    entry
                    for family in ("ipv4", "ipv6")
                    for entry in getattr(self.originated_prefixes, family) or ()
                    if entry not in declared
                }
            )
            if unknown_targets:
                raise ValueError(
                    "node originated_prefixes must name declared ethernet ports "
                    f"or default: {unknown_targets}"
                )
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
    node: NodeRef
    profile: ProfileRef | None = None
    terminals: dict[Identifier, TerminalInstallation]
    payloads: dict[Identifier, PayloadInstallation]
    # Binds each Ethernet port the referenced node definition declares to
    # one declared site segment, port id to segment id.
    interfaces: dict[SegmentId, SegmentId]
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
    # The network segments the site provides, through the same production a
    # node uses for the segments it carries. Subnets are resolver-allocated.
    ethernet: tuple[EthernetPort, ...] = Field(min_length=1)
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
    def _valid_segments_and_bindings(self) -> Site:
        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("site node ids must be unique")

        segment_ids = [segment.id for segment in self.ethernet]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("site ethernet segment ids must be unique")
        declared_segments = set(segment_ids)
        for node in self.nodes:
            unknown_segments = sorted(set(node.interfaces.values()) - declared_segments)
            if unknown_segments:
                raise ValueError(
                    f"site node {node.id!r} binds undeclared segment(s): {unknown_segments}"
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
    profile: ProfileRef | None = None
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


class ProfileDocument(_CatalogDocumentRoot):
    profile: Profile


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
