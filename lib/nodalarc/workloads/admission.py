# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structural admission for catalog workload profiles.

Admission wraps strict document validation into typed rejection evidence. The
catalog model already enforces schema, digest pinning, and the capability
vocabulary; the checks that remain here are deployment policy with their own
codes: namespace capability policy and per-container root-filesystem policy.

Admission is pure: it consumes a decoded document and returns a result object.
Nothing here evaluates a resolved world or touches deployment state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from nodalarc.catalog_refs import parse_catalog_reference
from nodalarc.models.catalog import Profile, ProfileDocument

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

AdmissionCode = Literal[
    "PROFILE_SCHEMA_INVALID",
    "PROFILE_CAPABILITY_NOT_ADMITTED",
    "PROFILE_ROOT_POLICY_NOT_ADMITTED",
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

    profile: Profile | None
    rejections: tuple[AdmissionEvidence, ...]

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> ProfileAdmission:
        if (self.profile is None) != bool(self.rejections):
            raise ValueError("profile admission must contain either a profile or rejections")
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
    error: ValidationError, *, object_ref: str
) -> list[AdmissionEvidence]:
    rejections = []
    for entry in error.errors():
        field_path = ".".join(str(part) for part in entry["loc"]) or None
        rejections.append(
            _evidence(
                "PROFILE_SCHEMA_INVALID",
                object_ref,
                entry["msg"],
                field_path=field_path,
            )
        )
    return rejections


def admit_profile(document: object, *, object_ref: str) -> ProfileAdmission:
    """Structurally admit one catalog profile document at its reference."""

    try:
        source = parse_catalog_reference(
            object_ref,
            expected_families=frozenset({"profiles"}),
            label="profile object reference",
        )
    except ValueError as error:
        return ProfileAdmission(
            profile=None,
            rejections=(_evidence("PROFILE_SCHEMA_INVALID", object_ref, str(error)),),
        )
    object_ref = f"{source.namespace}:{source.relative_path.as_posix()}"
    expected_id = source.relative_path.stem

    try:
        parsed = ProfileDocument.model_validate(document)
    except ValidationError as error:
        return ProfileAdmission(
            profile=None,
            rejections=tuple(_schema_rejections(error, object_ref=object_ref)),
        )

    profile = parsed.profile
    if profile.id != expected_id:
        return ProfileAdmission(
            profile=None,
            rejections=(
                _evidence(
                    "PROFILE_SCHEMA_INVALID",
                    object_ref,
                    f"profile id {profile.id!r} must match filename stem {expected_id!r}",
                    field_path="profile.id",
                ),
            ),
        )

    rejections: list[AdmissionEvidence] = []
    containers: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (profile.id, profile.capabilities, profile.root_filesystem),
        *(
            (sidecar.name, sidecar.capabilities, sidecar.root_filesystem)
            for sidecar in profile.sidecars
        ),
    )
    for name, capabilities, root_filesystem in containers:
        if source.namespace == "user" and capabilities:
            # Privileged user-namespace admission is a later, separately gated
            # enablement; when it opens, SYS_ADMIN stays outside it.
            rejections.append(
                _evidence(
                    "PROFILE_CAPABILITY_NOT_ADMITTED",
                    object_ref,
                    "user-namespace profiles admit no capabilities",
                    container=name,
                    examples=sorted(capabilities),
                )
            )
        if root_filesystem == "ephemeral_writable" and capabilities:
            rejections.append(
                _evidence(
                    "PROFILE_ROOT_POLICY_NOT_ADMITTED",
                    object_ref,
                    "an ephemeral_writable container must declare zero capabilities",
                    container=name,
                )
            )

    if rejections:
        return ProfileAdmission(profile=None, rejections=tuple(rejections))
    return ProfileAdmission(profile=profile, rejections=())
