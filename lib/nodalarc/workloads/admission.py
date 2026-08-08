# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structural admission for node-workload profiles and implementation bindings.

Admission wraps strict document validation into typed rejection evidence. The
checks that carry their own codes run here, after schema validation, so a
rejection names its exact rule instead of surfacing as a generic parse error:
image digest pinning, capability vocabulary and namespace policy, per-container
root-filesystem policy, and hook targets.

Admission is pure: it consumes a decoded document and returns a result object.
Nothing here evaluates selectors against a resolved world or touches
deployment state.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from nodalarc.catalog_refs import parse_catalog_reference
from nodalarc.workloads.binding import ImplementationBinding, ImplementationBindingDocument
from nodalarc.workloads.profile import (
    NodeWorkloadProfile,
    NodeWorkloadProfileDocument,
    ProfileContainer,
)

EVIDENCE_MAX_EXAMPLES = 20

CAPABILITY_VOCABULARY = frozenset(
    {
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
    }
)

_IMAGE_DIGEST_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")

AdmissionCode = Literal[
    "PROFILE_SCHEMA_INVALID",
    "PROFILE_IMAGE_NOT_DIGEST_PINNED",
    "PROFILE_CAPABILITY_NOT_ADMITTED",
    "PROFILE_ROOT_POLICY_NOT_ADMITTED",
    "PROFILE_HOOK_TARGET_INVALID",
    "BINDING_SCHEMA_INVALID",
]


class AdmissionEvidence(BaseModel):
    """One typed rejection with bounded structured examples."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: AdmissionCode
    object_ref: str
    field_path: str | None = None
    container: str | None = None
    examples: tuple[str, ...] = Field(default=(), max_length=EVIDENCE_MAX_EXAMPLES)
    detail: str


class ProfileAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: NodeWorkloadProfile | None
    rejections: tuple[AdmissionEvidence, ...]

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> ProfileAdmission:
        if (self.profile is None) != bool(self.rejections):
            raise ValueError("profile admission must contain either a profile or rejections")
        return self


class BindingAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    binding: ImplementationBinding | None
    rejections: tuple[AdmissionEvidence, ...]

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> BindingAdmission:
        if (self.binding is None) != bool(self.rejections):
            raise ValueError("binding admission must contain either a binding or rejections")
        return self


def _evidence(
    code: AdmissionCode,
    object_ref: str,
    detail: str,
    *,
    examples: list[str] | None = None,
    **fields: str | None,
) -> AdmissionEvidence:
    return AdmissionEvidence(
        code=code,
        object_ref=object_ref,
        detail=detail,
        examples=tuple(examples or [])[:EVIDENCE_MAX_EXAMPLES],
        **fields,
    )


def _schema_rejections(
    error: ValidationError, *, code: AdmissionCode, object_ref: str
) -> list[AdmissionEvidence]:
    rejections = []
    for entry in error.errors():
        field_path = ".".join(str(part) for part in entry["loc"]) or None
        rejections.append(
            _evidence(
                code,
                object_ref,
                entry["msg"],
                field_path=field_path,
            )
        )
    return rejections


def _all_containers(profile: NodeWorkloadProfile) -> tuple[ProfileContainer, ...]:
    return profile.init_containers + profile.workload_containers


def admit_profile(document: object, *, object_ref: str) -> ProfileAdmission:
    """Structurally admit one profile document at its package reference."""
    try:
        source = parse_catalog_reference(
            object_ref,
            expected_families=frozenset({"profiles"}),
            label="profile object reference",
        )
    except ValueError as error:
        return ProfileAdmission(
            profile=None,
            rejections=(
                _evidence(
                    "PROFILE_SCHEMA_INVALID",
                    object_ref,
                    str(error),
                ),
            ),
        )
    object_ref = f"{source.namespace}:{source.relative_path.as_posix()}"
    expected_id = source.relative_path.stem

    try:
        parsed = NodeWorkloadProfileDocument.model_validate(document)
    except ValidationError as error:
        return ProfileAdmission(
            profile=None,
            rejections=tuple(
                _schema_rejections(error, code="PROFILE_SCHEMA_INVALID", object_ref=object_ref)
            ),
        )

    profile = parsed.node_workload_profile
    if profile.id != expected_id:
        return ProfileAdmission(
            profile=None,
            rejections=(
                _evidence(
                    "PROFILE_SCHEMA_INVALID",
                    object_ref,
                    f"profile id {profile.id!r} must match filename stem {expected_id!r}",
                    field_path="node_workload_profile.id",
                ),
            ),
        )
    rejections: list[AdmissionEvidence] = []

    workload_names = {container.name for container in profile.workload_containers}

    for container in _all_containers(profile):
        if not _IMAGE_DIGEST_PATTERN.fullmatch(container.image):
            rejections.append(
                _evidence(
                    "PROFILE_IMAGE_NOT_DIGEST_PINNED",
                    object_ref,
                    f"image {container.image!r} is not digest-pinned",
                    container=container.name,
                )
            )

        unknown = sorted(set(container.capabilities) - CAPABILITY_VOCABULARY)
        if unknown:
            rejections.append(
                _evidence(
                    "PROFILE_CAPABILITY_NOT_ADMITTED",
                    object_ref,
                    "capabilities outside the closed vocabulary",
                    container=container.name,
                    examples=unknown,
                )
            )
        admitted = set(container.capabilities) & CAPABILITY_VOCABULARY
        if source.namespace == "user" and admitted:
            # Privileged user-namespace admission is a later, separately gated
            # enablement; when it opens, SYS_ADMIN stays outside it.
            rejections.append(
                _evidence(
                    "PROFILE_CAPABILITY_NOT_ADMITTED",
                    object_ref,
                    "user-namespace profiles admit no capabilities",
                    container=container.name,
                    examples=sorted(admitted),
                )
            )

        if container.root_filesystem == "ephemeral_writable":
            if container.capabilities:
                rejections.append(
                    _evidence(
                        "PROFILE_ROOT_POLICY_NOT_ADMITTED",
                        object_ref,
                        "an ephemeral_writable container must declare zero capabilities",
                        container=container.name,
                    )
                )
            if container.resources.ephemeral_storage_mi is None:
                rejections.append(
                    _evidence(
                        "PROFILE_ROOT_POLICY_NOT_ADMITTED",
                        object_ref,
                        "an ephemeral_writable container must declare an explicit "
                        "ephemeral-storage request and limit",
                        container=container.name,
                    )
                )
        elif container.resources.ephemeral_storage_mi is not None:
            rejections.append(
                _evidence(
                    "PROFILE_ROOT_POLICY_NOT_ADMITTED",
                    object_ref,
                    "ephemeral-storage declarations require an ephemeral_writable root",
                    container=container.name,
                )
            )

    if profile.readiness is not None and profile.readiness.container not in workload_names:
        rejections.append(
            _evidence(
                "PROFILE_HOOK_TARGET_INVALID",
                object_ref,
                "readiness must target a workload container",
                field_path="readiness",
                container=profile.readiness.container,
            )
        )

    if rejections:
        return ProfileAdmission(profile=None, rejections=tuple(rejections))
    return ProfileAdmission(profile=profile, rejections=())


def admit_binding(document: object, *, object_ref: str) -> BindingAdmission:
    """Structurally admit one implementation-binding document at its reference."""
    try:
        source = parse_catalog_reference(
            object_ref,
            expected_families=frozenset({"bindings"}),
            label="binding object reference",
        )
    except ValueError as error:
        return BindingAdmission(
            binding=None,
            rejections=(
                _evidence(
                    "BINDING_SCHEMA_INVALID",
                    object_ref,
                    str(error),
                ),
            ),
        )
    object_ref = f"{source.namespace}:{source.relative_path.as_posix()}"
    expected_id = source.relative_path.stem

    try:
        parsed = ImplementationBindingDocument.model_validate(document)
    except ValidationError as error:
        return BindingAdmission(
            binding=None,
            rejections=tuple(
                _schema_rejections(error, code="BINDING_SCHEMA_INVALID", object_ref=object_ref)
            ),
        )
    binding = parsed.implementation_binding
    if binding.id != expected_id:
        return BindingAdmission(
            binding=None,
            rejections=(
                _evidence(
                    "BINDING_SCHEMA_INVALID",
                    object_ref,
                    f"binding id {binding.id!r} must match filename stem {expected_id!r}",
                    field_path="implementation_binding.id",
                ),
            ),
        )
    return BindingAdmission(binding=binding, rejections=())
