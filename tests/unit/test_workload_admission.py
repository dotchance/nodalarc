# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog profile admission: schema evidence and namespace policy."""

from __future__ import annotations

from nodalarc.workloads.admission import CAPABILITY_VOCABULARY, admit_profile

_DIGEST = "0" * 64


def _profile(profile_id: str = "test-profile", **overrides) -> dict:
    document = {
        "id": profile_id,
        "registry": "registry.example",
        "image": f"nodalarc/base@sha256:{_DIGEST}",
        "command": ["/bin/bash", "-c", "sleep infinity"],
        "resources": {
            "requests": {"cpu_m": 10, "memory_mi": 16},
            "limits": {"cpu_m": 100, "memory_mi": 64},
        },
    }
    document.update(overrides)
    return {"profile": document}


def test_valid_shipped_profile_is_admitted() -> None:
    admission = admit_profile(
        _profile(capabilities=["NET_ADMIN", "NET_RAW"]),
        object_ref="nodalarc:profiles/test-profile.yaml",
    )

    assert admission.profile is not None
    assert admission.profile.id == "test-profile"
    assert admission.rejections == ()


def test_schema_failures_carry_typed_field_evidence() -> None:
    document = _profile()
    document["profile"]["image"] = "nodalarc/base:latest"

    admission = admit_profile(document, object_ref="nodalarc:profiles/test-profile.yaml")

    assert admission.profile is None
    assert all(evidence.code == "PROFILE_SCHEMA_INVALID" for evidence in admission.rejections)
    assert any(
        evidence.field_path and "image" in evidence.field_path
        for evidence in admission.rejections
    )


def test_profile_id_must_match_the_filename_stem() -> None:
    admission = admit_profile(_profile(), object_ref="nodalarc:profiles/other-name.yaml")

    assert admission.profile is None
    assert admission.rejections[0].code == "PROFILE_SCHEMA_INVALID"
    assert "filename stem" in admission.rejections[0].detail


def test_reference_outside_the_profiles_family_is_rejected() -> None:
    admission = admit_profile(_profile(), object_ref="nodalarc:nodes/test-profile.yaml")

    assert admission.profile is None
    assert admission.rejections[0].code == "PROFILE_SCHEMA_INVALID"


def test_user_namespace_profiles_admit_no_capabilities() -> None:
    admission = admit_profile(
        _profile(capabilities=["NET_ADMIN"]),
        object_ref="user:profiles/test-profile.yaml",
    )

    assert admission.profile is None
    assert admission.rejections[0].code == "PROFILE_CAPABILITY_NOT_ADMITTED"
    assert admission.rejections[0].examples == ("NET_ADMIN",)

    plain = admit_profile(_profile(), object_ref="user:profiles/test-profile.yaml")
    assert plain.profile is not None


def test_user_namespace_sidecar_capabilities_are_also_refused() -> None:
    document = _profile(
        sidecars=[
            {
                "name": "observer",
                "image": f"nodalarc/base@sha256:{_DIGEST}",
                "capabilities": ["NET_RAW"],
                "resources": {
                    "requests": {"cpu_m": 10, "memory_mi": 16},
                    "limits": {"cpu_m": 100, "memory_mi": 32},
                },
            }
        ]
    )

    admission = admit_profile(document, object_ref="user:profiles/test-profile.yaml")

    assert admission.profile is None
    assert admission.rejections[0].container == "observer"


def test_ephemeral_writable_requires_zero_capabilities() -> None:
    admission = admit_profile(
        _profile(root_filesystem="ephemeral_writable", capabilities=["CHOWN"]),
        object_ref="nodalarc:profiles/test-profile.yaml",
    )

    assert admission.profile is None
    assert admission.rejections[0].code == "PROFILE_ROOT_POLICY_NOT_ADMITTED"


def test_capability_vocabulary_matches_the_catalog_model() -> None:
    from typing import get_args

    from nodalarc.models.catalog import LinuxCapability

    assert CAPABILITY_VOCABULARY == set(get_args(LinuxCapability))
