# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Admission policy for workload profiles and implementation bindings."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from nodalarc.workloads.admission import admit_binding, admit_profile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "workloads"

FRR_REF = "nodalarc:profiles/frr/frr-reference.yaml"
STATIC_REF = "nodalarc:profiles/static-realizer.yaml"
ZERO_REF = "nodalarc:profiles/zero-capability.yaml"
BINDING_REF = "nodalarc:bindings/all-frr.yaml"

_PROFILE_FILES = {
    FRR_REF: FIXTURES / "profiles" / "frr" / "frr-reference.yaml",
    STATIC_REF: FIXTURES / "profiles" / "static-realizer.yaml",
    ZERO_REF: FIXTURES / "profiles" / "zero-capability.yaml",
}


def _profile_document(ref: str) -> dict[str, Any]:
    return yaml.safe_load(_PROFILE_FILES[ref].read_text())


def _binding_document() -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / "bindings" / "all-frr.yaml").read_text())


def _rejection_codes(result: Any) -> set[str]:
    return {rejection.code for rejection in result.rejections}


def test_fixture_profiles_admit() -> None:
    for ref in (FRR_REF, STATIC_REF, ZERO_REF):
        result = admit_profile(_profile_document(ref), object_ref=ref)
        assert result.profile is not None, result.rejections
        assert result.rejections == ()


def test_fixture_binding_admits() -> None:
    result = admit_binding(_binding_document(), object_ref=BINDING_REF)
    assert result.binding is not None, result.rejections
    assert result.binding.id == "all-frr"


def test_unpinned_image_is_rejected() -> None:
    document = _profile_document(ZERO_REF)
    document["node_workload_profile"]["workload_containers"][0]["image"] = (
        "registry.example/busybox:latest"
    )
    result = admit_profile(document, object_ref=ZERO_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_IMAGE_NOT_DIGEST_PINNED"}


def test_digest_pinned_image_with_trailing_newline_is_rejected() -> None:
    document = _profile_document(ZERO_REF)
    container = document["node_workload_profile"]["workload_containers"][0]
    container["image"] = container["image"] + "\n"
    result = admit_profile(document, object_ref=ZERO_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_IMAGE_NOT_DIGEST_PINNED"}


def test_capability_outside_vocabulary_is_rejected() -> None:
    document = _profile_document(ZERO_REF)
    document["node_workload_profile"]["workload_containers"][0]["capabilities"] = ["SYS_BOOT"]
    result = admit_profile(document, object_ref=ZERO_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_CAPABILITY_NOT_ADMITTED"}
    assert result.rejections[0].examples == ("SYS_BOOT",)


def test_user_namespace_profile_admits_no_capabilities() -> None:
    document = _profile_document(STATIC_REF)
    result = admit_profile(document, object_ref="user:profiles/static-realizer.yaml")
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_CAPABILITY_NOT_ADMITTED"}
    assert result.rejections[0].examples == ("NET_ADMIN",)


def test_user_namespace_zero_capability_profile_admits() -> None:
    document = _profile_document(ZERO_REF)
    result = admit_profile(document, object_ref="user:profiles/zero-capability.yaml")
    assert result.profile is not None


def test_ephemeral_writable_root_requires_zero_capabilities() -> None:
    document = _profile_document(STATIC_REF)
    container = document["node_workload_profile"]["workload_containers"][0]
    container["root_filesystem"] = "ephemeral_writable"
    container["resources"]["ephemeral_storage_mi"] = {"request": 16, "limit": 64}
    result = admit_profile(document, object_ref=STATIC_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_ROOT_POLICY_NOT_ADMITTED"}


def test_ephemeral_writable_root_requires_storage_bounds() -> None:
    document = _profile_document(ZERO_REF)
    container = document["node_workload_profile"]["workload_containers"][0]
    container["root_filesystem"] = "ephemeral_writable"
    result = admit_profile(document, object_ref=ZERO_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_ROOT_POLICY_NOT_ADMITTED"}


def test_storage_bounds_require_ephemeral_writable_root() -> None:
    document = _profile_document(ZERO_REF)
    container = document["node_workload_profile"]["workload_containers"][0]
    container["resources"]["ephemeral_storage_mi"] = {"request": 16, "limit": 64}
    result = admit_profile(document, object_ref=ZERO_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_ROOT_POLICY_NOT_ADMITTED"}


def test_readiness_must_target_a_workload_container() -> None:
    document = _profile_document(FRR_REF)
    document["node_workload_profile"]["readiness"]["container"] = "frr-adapter"
    result = admit_profile(document, object_ref=FRR_REF)
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_HOOK_TARGET_INVALID"}


def test_host_surface_requests_are_structurally_rejected() -> None:
    base = _profile_document(ZERO_REF)
    mutations: list[dict[str, Any]] = []

    privileged = deepcopy(base)
    privileged["node_workload_profile"]["workload_containers"][0]["privileged"] = True
    mutations.append(privileged)

    host_network = deepcopy(base)
    host_network["node_workload_profile"]["host_network"] = True
    mutations.append(host_network)

    service_account = deepcopy(base)
    service_account["node_workload_profile"]["service_account"] = "default"
    mutations.append(service_account)

    host_path = deepcopy(base)
    host_path["node_workload_profile"]["volumes"] = [
        {"name": "host", "kind": "host_path", "medium": "node", "size_mi": 1}
    ]
    mutations.append(host_path)

    device_mount = deepcopy(base)
    device_mount["node_workload_profile"]["volumes"] = [
        {"name": "scratch", "kind": "ephemeral", "medium": "memory", "size_mi": 1}
    ]
    device_mount["node_workload_profile"]["workload_containers"][0]["volume_mounts"] = [
        {"volume": "scratch", "path": "/dev/shm", "read_only": False}
    ]
    mutations.append(device_mount)

    secrets_mount = deepcopy(base)
    secrets_mount["node_workload_profile"]["volumes"] = [
        {"name": "scratch", "kind": "ephemeral", "medium": "memory", "size_mi": 1}
    ]
    secrets_mount["node_workload_profile"]["workload_containers"][0]["volume_mounts"] = [
        {"volume": "scratch", "path": "/var/run/secrets/kubernetes.io", "read_only": True}
    ]
    mutations.append(secrets_mount)

    for document in mutations:
        result = admit_profile(document, object_ref=ZERO_REF)
        assert result.profile is None
        assert _rejection_codes(result) == {"PROFILE_SCHEMA_INVALID"}


def test_profile_id_must_match_reference_stem() -> None:
    document = _profile_document(ZERO_REF)
    result = admit_profile(document, object_ref="nodalarc:profiles/other-name.yaml")
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_SCHEMA_INVALID"}


def test_profile_reference_must_use_the_profiles_family() -> None:
    document = _profile_document(ZERO_REF)
    result = admit_profile(document, object_ref="nodalarc:sessions/zero-capability.yaml")
    assert result.profile is None
    assert _rejection_codes(result) == {"PROFILE_SCHEMA_INVALID"}


def test_binding_duplicate_entry_ids_are_rejected() -> None:
    document = _binding_document()
    entry = deepcopy(document["implementation_binding"]["entries"][0])
    entry["selector"] = {"node_kind": "satellite"}
    document["implementation_binding"]["entries"].append(entry)
    result = admit_binding(document, object_ref=BINDING_REF)
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}


def test_binding_permits_at_most_one_remainder() -> None:
    document = _binding_document()
    entry = deepcopy(document["implementation_binding"]["entries"][0])
    entry["id"] = "second-remainder"
    document["implementation_binding"]["entries"].append(entry)
    result = admit_binding(document, object_ref=BINDING_REF)
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}


def test_binding_selector_requires_exactly_one_member() -> None:
    document = _binding_document()
    document["implementation_binding"]["entries"][0]["selector"] = {
        "node_kind": "satellite",
        "tag": "extra",
    }
    result = admit_binding(document, object_ref=BINDING_REF)
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}

    document["implementation_binding"]["entries"][0]["selector"] = {}
    result = admit_binding(document, object_ref=BINDING_REF)
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}


def test_binding_requires_entries() -> None:
    document = _binding_document()
    document["implementation_binding"]["entries"] = []
    result = admit_binding(document, object_ref=BINDING_REF)
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}


def test_binding_reference_must_use_the_bindings_family() -> None:
    document = _binding_document()
    result = admit_binding(document, object_ref="nodalarc:profiles/all-frr.yaml")
    assert result.binding is None
    assert _rejection_codes(result) == {"BINDING_SCHEMA_INVALID"}
