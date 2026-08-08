# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Translator contract: pure, identity-bound, profile-declared delivery only."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nodalarc.workloads.plan import compile_workload_plan
from nodalarc.workloads.refs import ImplementationBindingRef, ProfileRef
from nodalarc.workloads.resolution import NodeAssignment, WorkloadSelection
from nodalarc.workloads.source import DirectoryPackageSource
from nodalarc_operator.workloads.compose import compose_workload

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "workloads"
BINDING_REF = ImplementationBindingRef("nodalarc:bindings/all-frr.yaml")
FRR_REF = ProfileRef("nodalarc:profiles/frr/frr-reference.yaml")
OWNER_REF = {"kind": "ConstellationSpec", "name": "s", "uid": "owner-uid-1"}
RENDERED = b"hostname sat-a\n"


def _package():
    return DirectoryPackageSource(FIXTURES).load(BINDING_REF)


def _plan(package, artifacts=None):
    selection = WorkloadSelection(
        binding_ref=package.binding_ref,
        package_digest=package.package_digest,
        assignments=(NodeAssignment(node_id="sat-A", entry_id="everything", profile_ref=FRR_REF),),
    )
    return compile_workload_plan(
        selection,
        package,
        "sat-A",
        plan_artifacts=artifacts if artifacts is not None else {"frr.conf": RENDERED},
    )


def _compose(package=None, plan=None):
    package = package or _package()
    plan = plan or _plan(package)
    return compose_workload(plan, package, namespace="nodalarc", owner_ref=OWNER_REF)


def test_translation_covers_profile_and_delivers_artifacts() -> None:
    package = _package()
    composed = _compose(package=package)
    composition = composed.composition

    # Authored containers, translated one to one; no additions, no probes
    # or provider behavior invented.
    assert [c.name for c in composition.init_containers] == ["frr-adapter"]
    assert [c.name for c in composition.containers] == ["frr"]
    frr = composition.containers[0]
    profile = package.profiles[str(FRR_REF)].profile
    assert frr.image == profile.workload_containers[0].image

    # Platform-generated security context from the admitted declarations.
    assert frr.security_context.capabilities.drop == ["ALL"]
    assert frr.security_context.capabilities.add == list(
        profile.workload_containers[0].capabilities
    )
    assert frr.security_context.read_only_root_filesystem is True
    assert frr.security_context.allow_privilege_escalation is False
    adapter = composition.init_containers[0]
    assert adapter.security_context.capabilities.add is None
    assert adapter.readiness_probe is None
    assert frr.readiness_probe is not None

    # The artifact ConfigMap is content-addressed binaryData with exact bytes.
    cm = composed.artifact_config_map
    assert cm is not None
    assert cm.immutable is True
    assert cm.metadata.owner_references == [OWNER_REF]
    assert base64.b64decode(cm.binary_data["plan-frr.conf"]) == RENDERED
    daemons = (FIXTURES / "profiles" / "frr" / "daemons").read_bytes()
    assert base64.b64decode(cm.binary_data["static-daemons"]) == daemons
    assert cm.metadata.name.startswith("wl-sat-a-")

    # Mounts land only at profile-declared destinations.
    frr_mounts = {mount.mount_path: mount for mount in frr.volume_mounts}
    static_mount = frr_mounts["/etc/frr-static/daemons"]
    assert static_mount.sub_path == "static-daemons"
    assert static_mount.read_only is True
    plan_mount = frr_mounts["/etc/frr-plan"]
    assert plan_mount.read_only is True
    adapter_paths = {mount.mount_path for mount in adapter.volume_mounts}
    assert "/etc/frr-plan" not in adapter_paths

    # Same-name content addressing is stable.
    assert _compose(package=package).artifact_config_map.metadata.name == cm.metadata.name


def test_plan_from_another_package_is_refused() -> None:
    from nodalarc.workloads.plan import WorkloadPlan

    package = _package()
    foreign = WorkloadPlan(
        node_id="sat-A", profile_ref=FRR_REF, package_digest="sha256:" + "e" * 64
    )
    with pytest.raises(ValueError, match="not the loaded package"):
        compose_workload(foreign, package, namespace="nodalarc", owner_ref=OWNER_REF)


def test_no_artifacts_means_no_config_map() -> None:
    package = _package()
    plan = _plan(package, artifacts={})
    composed = compose_workload(plan, package, namespace="nodalarc", owner_ref=OWNER_REF)
    # The frr fixture ships a static file, so a map still exists; its plan
    # volume must be absent while the static delivery remains.
    volume_names = {volume.name for volume in composed.composition.volumes}
    assert "na-plan-artifacts" not in volume_names
    assert "na-static-artifacts" in volume_names


def test_nested_artifact_names_are_refused() -> None:
    package = _package()
    with pytest.raises(ValueError, match="flat"):
        _compose(package=package, plan=_plan(package, artifacts={"conf/frr.conf": b"x"}))
