# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Node-workload profile document model (v1).

A profile describes one complete authored node workload: container
composition, capabilities, volumes, package-carried artifact inputs, routing
realization, and optional readiness behavior. It is not a PodSpec and
deliberately exposes no arbitrary Kubernetes surface.

This module owns document structure only. Checks that carry their own
admission codes (image digest pinning, capability vocabulary and namespace
policy, root-filesystem policy, hook targets) live in
``nodalarc.workloads.admission`` so their rejections are typed, not generic
schema errors.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from nodalarc.catalog_refs import validate_catalog_name
from nodalarc.content_identity import BARE_SHA256_PATTERN
from nodalarc.model_validation import StrictBoolean, StrictInteger

DNS_LABEL_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"

DnsLabel = Annotated[str, StringConstraints(pattern=DNS_LABEL_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern=BARE_SHA256_PATTERN)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

FORBIDDEN_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/var/run/secrets")

ARGV_MAX_ELEMENTS = 64
ARGV_MAX_TOTAL_BYTES = 4096
DESCRIPTION_MAX_BYTES = 2048

RoutingCapabilityToken = Literal["mpls", "segment_routing", "traffic_engineering"]
RealizedProtocol = Literal["isis", "ospf", "bgp", "static"]


def _catalog_object_name(value: str) -> str:
    return validate_catalog_name(value, label="catalog object id")


def _required_true(value: object) -> object:
    if value is not True:
        raise ValueError("must be the YAML boolean true")
    return value


CatalogName = Annotated[str, AfterValidator(_catalog_object_name)]
RequiredTrue = Annotated[Literal[True], BeforeValidator(_required_true)]


def validate_mount_path(path: str) -> str:
    """One normalized absolute POSIX mount path, outside reserved trees."""
    if "\x00" in path:
        raise ValueError("mount path must not contain NUL")
    if not path.startswith("/") or path == "/":
        raise ValueError(f"mount path must be absolute and not '/': {path!r}")
    if path.endswith("/"):
        raise ValueError(f"mount path must not end with '/': {path!r}")
    if "//" in path:
        raise ValueError(f"mount path must not contain '//': {path!r}")
    if not unicodedata.is_normalized("NFC", path):
        raise ValueError(f"mount path must be NFC-normalized: {path!r}")
    segments = path.split("/")[1:]
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError(f"mount path must not contain '.' or '..' segments: {path!r}")
    for prefix in FORBIDDEN_MOUNT_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            raise ValueError(f"mount path is under a reserved tree: {path!r}")
    return path


def validate_package_relative_path(path: str) -> str:
    """One normalized package-relative file path."""
    if "\x00" in path:
        raise ValueError("package path must not contain NUL")
    if "\\" in path:
        raise ValueError(f"package path must not contain backslashes: {path!r}")
    if not path or path.startswith("/") or path.endswith("/"):
        raise ValueError(f"package path must be relative and non-empty: {path!r}")
    if "//" in path:
        raise ValueError(f"package path must not contain '//': {path!r}")
    if not unicodedata.is_normalized("NFC", path):
        raise ValueError(f"package path must be NFC-normalized: {path!r}")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError(f"package path must not contain '.' or '..' segments: {path!r}")
    return path


def _validate_argv(argv: tuple[str, ...], *, field: str) -> None:
    if not argv:
        raise ValueError(f"{field} must be a nonempty list")
    if any(not element for element in argv):
        raise ValueError(f"{field} elements must be nonempty")
    if any("\x00" in element for element in argv):
        raise ValueError(f"{field} elements must not contain NUL")
    if len(argv) > ARGV_MAX_ELEMENTS:
        raise ValueError(f"{field} exceeds {ARGV_MAX_ELEMENTS} elements")
    total = sum(len(element.encode()) for element in argv)
    if total > ARGV_MAX_TOTAL_BYTES:
        raise ValueError(f"{field} exceeds {ARGV_MAX_TOTAL_BYTES} bytes total")


def _paths_conflict(path_a: str, path_b: str) -> bool:
    return path_a == path_b or path_a.startswith(f"{path_b}/") or path_b.startswith(f"{path_a}/")


class ProfileVolume(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: DnsLabel
    kind: Literal["ephemeral"]
    medium: Literal["memory", "node"]
    size_mi: StrictInteger = Field(ge=1, le=1024)


class ResourceAmounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu_m: StrictInteger = Field(gt=0)
    memory_mi: StrictInteger = Field(gt=0)


class EphemeralStorage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: StrictInteger = Field(gt=0)
    limit: StrictInteger = Field(gt=0)

    @model_validator(mode="after")
    def _limit_covers_request(self) -> EphemeralStorage:
        if self.limit < self.request:
            raise ValueError("ephemeral-storage limit must be >= request")
        return self


class ContainerResources(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requests: ResourceAmounts
    limits: ResourceAmounts
    ephemeral_storage_mi: EphemeralStorage | None = None

    @model_validator(mode="after")
    def _limits_cover_requests(self) -> ContainerResources:
        if self.limits.cpu_m < self.requests.cpu_m:
            raise ValueError("cpu limit must be >= cpu request")
        if self.limits.memory_mi < self.requests.memory_mi:
            raise ValueError("memory limit must be >= memory request")
        return self


class VolumeMount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    volume: DnsLabel
    path: NonEmptyStr
    read_only: StrictBoolean

    @field_validator("path")
    @classmethod
    def _path_rules(cls, path: str) -> str:
        return validate_mount_path(path)


class ProfileContainer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: DnsLabel
    image: NonEmptyStr
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    resources: ContainerResources
    capabilities: tuple[str, ...] = ()
    root_filesystem: Literal["read_only", "ephemeral_writable"] = "read_only"
    volume_mounts: tuple[VolumeMount, ...] = ()

    @field_validator("command", "args")
    @classmethod
    def _argv_rules(
        cls, argv: tuple[str, ...] | None, info: ValidationInfo
    ) -> tuple[str, ...] | None:
        if argv is not None:
            _validate_argv(argv, field=info.field_name)
        return argv

    @model_validator(mode="after")
    def _container_rules(self) -> ProfileContainer:
        if list(self.capabilities) != sorted(set(self.capabilities)):
            raise ValueError("capabilities must be sorted and unique")
        mounts = [mount.path for mount in self.volume_mounts]
        for index, path in enumerate(mounts):
            for other in mounts[index + 1 :]:
                if _paths_conflict(path, other):
                    raise ValueError(f"mount paths conflict: {path!r} and {other!r}")
        return self


class StaticArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: NonEmptyStr
    sha256: Sha256Hex
    container: DnsLabel
    path: NonEmptyStr
    read_only: RequiredTrue

    @field_validator("file")
    @classmethod
    def _file_rules(cls, path: str) -> str:
        return validate_package_relative_path(path)

    @field_validator("path")
    @classmethod
    def _path_rules(cls, path: str) -> str:
        return validate_mount_path(path)


class PlanArtifactMount(BaseModel):
    """The one profile-declared destination for per-node plan artifacts.

    A plan carries pre-rendered per-node bytes (for example a rendered
    native configuration set); this slot names exactly which container
    receives them and where. Without this declaration a profile accepts no
    plan artifacts — the platform never invents a destination.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    container: DnsLabel
    path: NonEmptyStr
    read_only: RequiredTrue

    @field_validator("path")
    @classmethod
    def _path_rules(cls, path: str) -> str:
        return validate_mount_path(path)


class ProfileArtifacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    static: tuple[StaticArtifact, ...] = Field(default=(), max_length=8)
    plan: PlanArtifactMount | None = None

    @model_validator(mode="after")
    def _artifact_rules(self) -> ProfileArtifacts:
        files = [artifact.file for artifact in self.static]
        if len(set(files)) != len(files):
            raise ValueError("static artifact files must be unique")
        return self


class RealizationEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: RealizedProtocol
    capabilities: tuple[RoutingCapabilityToken, ...]

    @model_validator(mode="after")
    def _entry_rules(self) -> RealizationEntry:
        if list(self.capabilities) != sorted(set(self.capabilities)):
            raise ValueError("realization capabilities must be sorted and unique")
        if self.protocol == "static" and self.capabilities:
            raise ValueError("static realization must declare an empty capability set")
        return self


class RoutingRealization(BaseModel):
    """Either an explicit no-realization declaration or one owning container."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    none: RequiredTrue | None = None
    container: DnsLabel | None = None
    realizes: tuple[RealizationEntry, ...] | None = Field(default=None, max_length=4)

    @model_validator(mode="after")
    def _exactly_one_form(self) -> RoutingRealization:
        declared = self.container is not None or self.realizes is not None
        if self.none is True and declared:
            raise ValueError("routing_realization is either none or a declaration, not both")
        if self.none is not True and not (self.container is not None and self.realizes):
            raise ValueError(
                "routing_realization requires none: true, or a container with realizes entries"
            )
        if self.realizes is not None:
            protocols = [entry.protocol for entry in self.realizes]
            if protocols != sorted(set(protocols)):
                raise ValueError("realization protocols must be sorted and unique")
        return self


class ReadinessHook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    container: DnsLabel
    argv: tuple[str, ...]
    timeout_seconds: StrictInteger = Field(ge=1, le=300)
    period_seconds: StrictInteger = Field(ge=1, le=60)

    @field_validator("argv")
    @classmethod
    def _argv_rules(cls, argv: tuple[str, ...]) -> tuple[str, ...]:
        _validate_argv(argv, field="argv")
        return argv


class NodeWorkloadProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    id: CatalogName
    description: NonEmptyStr
    volumes: tuple[ProfileVolume, ...] = Field(default=(), max_length=16)
    init_containers: tuple[ProfileContainer, ...] = Field(default=(), max_length=8)
    workload_containers: tuple[ProfileContainer, ...] = Field(
        json_schema_extra={"minItems": 1, "maxItems": 8}
    )
    artifacts: ProfileArtifacts = ProfileArtifacts()
    routing_realization: RoutingRealization
    readiness: ReadinessHook | None = None

    @field_validator("workload_containers", mode="before")
    @classmethod
    def _workload_count(cls, containers: object) -> object:
        if isinstance(containers, list | tuple) and not 1 <= len(containers) <= 8:
            raise ValueError("workload_containers must contain 1 through 8 entries")
        return containers

    @field_validator("description")
    @classmethod
    def _description_rules(cls, description: str) -> str:
        if len(description.encode()) > DESCRIPTION_MAX_BYTES:
            raise ValueError(f"description exceeds {DESCRIPTION_MAX_BYTES} bytes")
        return description

    @model_validator(mode="after")
    def _document_rules(self) -> NodeWorkloadProfile:
        volume_names = [volume.name for volume in self.volumes]
        if len(set(volume_names)) != len(volume_names):
            raise ValueError("volume names must be unique")
        declared_volumes = set(volume_names)

        containers = self.init_containers + self.workload_containers
        container_names = [container.name for container in containers]
        if len(set(container_names)) != len(container_names):
            raise ValueError("container names must be unique across init and workload lists")
        known_containers = set(container_names)
        workload_names = {container.name for container in self.workload_containers}

        realization_container = self.routing_realization.container
        if realization_container is not None and realization_container not in workload_names:
            raise ValueError("routing_realization must name a workload container")

        mounts_by_container: dict[str, list[str]] = {name: [] for name in container_names}
        for container in containers:
            for mount in container.volume_mounts:
                if mount.volume not in declared_volumes:
                    raise ValueError(
                        f"container {container.name!r} mounts undeclared volume {mount.volume!r}"
                    )
                mounts_by_container[container.name].append(mount.path)

        for artifact in self.artifacts.static:
            if artifact.container not in known_containers:
                raise ValueError(
                    f"static artifact references unknown container {artifact.container!r}"
                )
            mounts_by_container[artifact.container].append(artifact.path)

        if self.artifacts.plan is not None:
            if self.artifacts.plan.container not in known_containers:
                raise ValueError(
                    f"plan artifact mount references unknown container "
                    f"{self.artifacts.plan.container!r}"
                )
            mounts_by_container[self.artifacts.plan.container].append(self.artifacts.plan.path)

        for name, paths in mounts_by_container.items():
            for index, path in enumerate(paths):
                for other in paths[index + 1 :]:
                    if _paths_conflict(path, other):
                        raise ValueError(
                            f"container {name!r} mount paths conflict: {path!r} and {other!r}"
                        )
        return self


class NodeWorkloadProfileDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_workload_profile: NodeWorkloadProfile
