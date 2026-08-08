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
    plan_volume = next(v for v in composition.volumes if v.name == "na-plan-artifacts")
    (item,) = plan_volume.config_map.items
    assert item.path == "frr.conf"
    assert base64.b64decode(cm.binary_data[item.key]) == RENDERED
    daemons = (FIXTURES / "profiles" / "frr" / "daemons").read_bytes()
    frr_static_mount = next(
        m
        for c in composition.containers
        for m in c.volume_mounts
        if m.mount_path == "/etc/frr-static/daemons"
    )
    assert base64.b64decode(cm.binary_data[frr_static_mount.sub_path]) == daemons
    # Name carries node, CR incarnation, and content identity.
    assert cm.metadata.name.startswith("wl-sat-a-owneruid")

    # Mounts land only at profile-declared destinations.
    frr_mounts = {mount.mount_path: mount for mount in frr.volume_mounts}
    static_mount = frr_mounts["/etc/frr-static/daemons"]
    assert static_mount.sub_path.startswith("s-")
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


def test_nested_artifact_paths_round_trip_through_safe_keys() -> None:
    """Admitted paths may nest; keys are opaque and safe, and the original
    path is preserved through the projection."""
    package = _package()
    composed = _compose(
        package=package, plan=_plan(package, artifacts={"conf/frr.conf": b"nested"})
    )
    plan_volume = next(v for v in composed.composition.volumes if v.name == "na-plan-artifacts")
    (item,) = plan_volume.config_map.items
    assert item.path == "conf/frr.conf"
    assert "/" not in item.key
    assert base64.b64decode(composed.artifact_config_map.binary_data[item.key]) == b"nested"


def test_ephemeral_writable_zero_capability_security_context(tmp_path: Path) -> None:
    """The admitted security exception translates exactly: drop-all with no
    adds, writable root, no escalation, exact ephemeral-storage bounds."""
    import shutil

    import yaml

    root = tmp_path / "package"
    shutil.copytree(FIXTURES / "profiles", root / "profiles")
    profile_path = root / "profiles" / "zero-capability.yaml"
    document = yaml.safe_load(profile_path.read_text())
    container = document["node_workload_profile"]["workload_containers"][0]
    container["root_filesystem"] = "ephemeral_writable"
    container["resources"]["ephemeral_storage_mi"] = {"request": 16, "limit": 64}
    profile_path.write_text(yaml.safe_dump(document))
    (root / "bindings").mkdir()
    (root / "bindings" / "all-zero.yaml").write_text(
        yaml.safe_dump(
            {
                "implementation_binding": {
                    "schema_version": "1",
                    "id": "all-zero",
                    "description": "Bind everything to the writable zero-cap profile.",
                    "entries": [
                        {
                            "id": "everything",
                            "selector": {"remainder": True},
                            "profile": "nodalarc:profiles/zero-capability.yaml",
                        }
                    ],
                }
            }
        )
    )
    package = DirectoryPackageSource(root).load(
        ImplementationBindingRef("nodalarc:bindings/all-zero.yaml")
    )
    selection = WorkloadSelection(
        binding_ref=package.binding_ref,
        package_digest=package.package_digest,
        assignments=(
            NodeAssignment(
                node_id="sat-A",
                entry_id="everything",
                profile_ref=ProfileRef("nodalarc:profiles/zero-capability.yaml"),
            ),
        ),
    )
    plan = compile_workload_plan(selection, package, "sat-A")
    composed = compose_workload(plan, package, namespace="nodalarc", owner_ref=OWNER_REF)
    (app,) = composed.composition.containers
    assert app.security_context.capabilities.drop == ["ALL"]
    assert app.security_context.capabilities.add is None
    assert app.security_context.read_only_root_filesystem is False
    assert app.security_context.allow_privilege_escalation is False
    assert app.resources.requests["ephemeral-storage"] == "16Mi"
    assert app.resources.limits["ephemeral-storage"] == "64Mi"
