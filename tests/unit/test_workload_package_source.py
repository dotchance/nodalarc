# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Workload package loading: byte retention, one identity, typed refusals."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from nodalarc.workloads.refs import ImplementationBindingRef, ProfileRef
from nodalarc.workloads.source import (
    DirectoryPackageSource,
    LoadedPackage,
    LoadedProfile,
    PackageLoadCode,
    PackageLoadError,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "workloads"

BINDING_REF = ImplementationBindingRef("nodalarc:bindings/all-frr.yaml")
FRR_REF = "nodalarc:profiles/frr/frr-reference.yaml"


def _fixture_copy(tmp_path: Path) -> Path:
    root = tmp_path / "package"
    shutil.copytree(FIXTURES, root)
    return root


def test_load_retains_exact_bytes_and_admitted_models() -> None:
    package = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    assert package.binding.id == "all-frr"
    assert package.document_bytes == (FIXTURES / "bindings" / "all-frr.yaml").read_bytes()
    loaded = package.profiles[FRR_REF]
    assert loaded.profile.id == "frr-reference"
    assert (
        loaded.document_bytes == (FIXTURES / "profiles" / "frr" / "frr-reference.yaml").read_bytes()
    )
    assert loaded.files["daemons"] == (FIXTURES / "profiles" / "frr" / "daemons").read_bytes()


def test_package_digest_is_stable_and_content_addressed(tmp_path: Path) -> None:
    original = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    relocated = DirectoryPackageSource(_fixture_copy(tmp_path)).load(BINDING_REF)
    assert original.package_digest == relocated.package_digest
    assert original.package_digest.startswith("sha256:")


def test_changed_bytes_change_the_package_digest(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    binding_path = root / "bindings" / "all-frr.yaml"
    binding_path.write_bytes(binding_path.read_bytes() + b"# trailing comment\n")
    changed = DirectoryPackageSource(root).load(BINDING_REF)
    original = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    assert changed.package_digest != original.package_digest


def test_missing_binding_document_is_a_typed_refusal() -> None:
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(FIXTURES).load(
            ImplementationBindingRef("nodalarc:bindings/absent.yaml")
        )
    assert excinfo.value.code == PackageLoadCode.PACKAGE_DOCUMENT_MISSING


def test_missing_profile_document_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    binding_path = root / "bindings" / "all-frr.yaml"
    document = yaml.safe_load(binding_path.read_text())
    document["implementation_binding"]["entries"][0]["profile"] = "nodalarc:profiles/absent.yaml"
    binding_path.write_text(yaml.safe_dump(document))
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_DOCUMENT_MISSING


def test_profile_admission_rejections_propagate(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    profile_path = root / "profiles" / "frr" / "frr-reference.yaml"
    document = yaml.safe_load(profile_path.read_text())
    document["node_workload_profile"]["workload_containers"][0]["image"] = "frr:latest"
    profile_path.write_text(yaml.safe_dump(document))
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_DOCUMENT_INVALID
    assert any(
        rejection.code == "PROFILE_IMAGE_NOT_DIGEST_PINNED"
        for rejection in excinfo.value.rejections
    )


def test_missing_declared_file_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    (root / "profiles" / "frr" / "daemons").unlink()
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_FILE_MISSING


def test_mismatched_declared_file_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    daemons = root / "profiles" / "frr" / "daemons"
    daemons.write_bytes(daemons.read_bytes() + b"bgpd=yes\n")
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_FILE_MISMATCH


def test_reference_outside_source_namespace_is_a_typed_refusal() -> None:
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(FIXTURES).load(
            ImplementationBindingRef("user:bindings/all-frr.yaml")
        )
    assert excinfo.value.code == PackageLoadCode.PACKAGE_REF_INVALID


def test_invalid_yaml_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    (root / "bindings" / "all-frr.yaml").write_text("{unbalanced: [")
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_DOCUMENT_INVALID


def test_symlinked_directory_outside_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(FIXTURES, outside)
    root = tmp_path / "package"
    root.mkdir()
    (root / "bindings").symlink_to(outside / "bindings", target_is_directory=True)
    shutil.copytree(FIXTURES / "profiles", root / "profiles")
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_PATH_ESCAPE


def test_symlinked_file_outside_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside-daemons"
    outside.write_bytes((FIXTURES / "profiles" / "frr" / "daemons").read_bytes())
    root = _fixture_copy(tmp_path)
    daemons = root / "profiles" / "frr" / "daemons"
    daemons.unlink()
    daemons.symlink_to(outside)
    with pytest.raises(PackageLoadError) as excinfo:
        DirectoryPackageSource(root).load(BINDING_REF)
    assert excinfo.value.code == PackageLoadCode.PACKAGE_PATH_ESCAPE


def test_loaded_profile_rejects_model_not_derived_from_bytes() -> None:
    package = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    loaded = package.profiles[FRR_REF]
    document = yaml.safe_load(loaded.document_bytes)
    document["node_workload_profile"]["description"] = "Recut description."
    with pytest.raises(ValueError, match="does not derive"):
        LoadedProfile(
            ref=ProfileRef(FRR_REF),
            profile=loaded.profile,
            document_bytes=yaml.safe_dump(document).encode(),
            files=dict(loaded.files),
        )


def test_loaded_package_rejects_model_not_derived_from_bytes() -> None:
    package = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    document = yaml.safe_load(package.document_bytes)
    document["implementation_binding"]["description"] = "Recut description."
    with pytest.raises(ValueError, match="does not derive"):
        LoadedPackage(
            binding_ref=package.binding_ref,
            binding=package.binding,
            document_bytes=yaml.safe_dump(document).encode(),
            profiles=dict(package.profiles),
        )


def test_loaded_package_requires_the_exact_referenced_profile_set() -> None:
    package = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    with pytest.raises(ValueError, match="referenced set exactly"):
        LoadedPackage(
            binding_ref=package.binding_ref,
            binding=package.binding,
            document_bytes=package.document_bytes,
            profiles={},
        )


def test_loaded_profile_rejects_bytes_that_contradict_declarations() -> None:
    package = DirectoryPackageSource(FIXTURES).load(BINDING_REF)
    loaded = package.profiles[FRR_REF]
    with pytest.raises(ValueError, match="declarations exactly"):
        LoadedProfile(
            ref=ProfileRef(FRR_REF),
            profile=loaded.profile,
            document_bytes=loaded.document_bytes,
            files={"daemons": b"zebra=no\n"},
        )
