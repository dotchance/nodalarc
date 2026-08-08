# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload plan envelope: selection-bound identity, retained bytes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from nodalarc.workloads.plan import compile_workload_plan
from nodalarc.workloads.refs import ImplementationBindingRef, ProfileRef
from nodalarc.workloads.resolution import NodeAssignment, WorkloadSelection
from nodalarc.workloads.source import DirectoryPackageSource

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "workloads"
BINDING_REF = ImplementationBindingRef("nodalarc:bindings/all-frr.yaml")
FRR_REF = ProfileRef("nodalarc:profiles/frr/frr-reference.yaml")
ZERO_REF = ProfileRef("nodalarc:profiles/zero-capability.yaml")


def _package():
    return DirectoryPackageSource(FIXTURES).load(BINDING_REF)


def _selection(package, node_id: str = "sat-a", profile_ref=FRR_REF, **overrides):
    fields = {
        "binding_ref": package.binding_ref,
        "package_digest": package.package_digest,
        "assignments": (
            NodeAssignment(node_id=node_id, entry_id="everything", profile_ref=profile_ref),
        ),
    }
    fields.update(overrides)
    return WorkloadSelection(**fields)


def test_plan_carries_selection_identity_and_artifact_bytes() -> None:
    package = _package()
    rendered = b"hostname sat-a\n"
    plan = compile_workload_plan(
        _selection(package), package, "sat-a", plan_artifacts={"frr.conf": rendered}
    )
    assert plan.node_id == "sat-a"
    assert plan.profile_ref == FRR_REF
    assert plan.package_digest == package.package_digest
    assert plan.plan_artifacts["frr.conf"] == rendered
    with pytest.raises(TypeError):
        plan.plan_artifacts["frr.conf"] = b"mutated"  # type: ignore[index]


def test_selection_from_another_package_is_refused() -> None:
    """An assignment resolved from package A must never compile against
    package B, even when both contain the same profile reference."""
    package = _package()
    foreign = _selection(package, package_digest="sha256:" + "e" * 64)
    with pytest.raises(ValueError, match="not the loaded package"):
        compile_workload_plan(foreign, package, "sat-a")


def test_node_outside_the_selection_is_refused() -> None:
    package = _package()
    with pytest.raises(ValueError, match="does not belong to this selection"):
        compile_workload_plan(_selection(package), package, "sat-z")


def test_artifacts_without_a_declared_destination_are_refused(tmp_path: Path) -> None:
    """The platform never invents where plan bytes land: a profile with no
    plan slot accepts no plan artifacts."""
    root = tmp_path / "package"
    shutil.copytree(FIXTURES / "profiles", root / "profiles")
    (root / "bindings").mkdir()
    (root / "bindings" / "all-zero.yaml").write_text(
        yaml.safe_dump(
            {
                "implementation_binding": {
                    "schema_version": "1",
                    "id": "all-zero",
                    "description": "Bind everything to the slotless profile.",
                    "entries": [
                        {
                            "id": "everything",
                            "selector": {"remainder": True},
                            "profile": str(ZERO_REF),
                        }
                    ],
                }
            }
        )
    )
    package = DirectoryPackageSource(root).load(
        ImplementationBindingRef("nodalarc:bindings/all-zero.yaml")
    )
    selection = _selection(package, profile_ref=ZERO_REF)
    with pytest.raises(ValueError, match="no plan-artifact destination"):
        compile_workload_plan(selection, package, "sat-a", plan_artifacts={"x.conf": b"x"})


def test_plan_artifact_names_are_contained_paths() -> None:
    package = _package()
    with pytest.raises(ValueError):
        compile_workload_plan(
            _selection(package), package, "sat-a", plan_artifacts={"../escape": b"x"}
        )


def test_assigned_profile_absent_from_package_is_refused() -> None:
    """The package-profile lookup is unconditional: a matching selection
    naming a profile the package does not contain refuses even with no
    artifacts supplied."""
    package = _package()
    selection = _selection(package, profile_ref=ZERO_REF)
    with pytest.raises(ValueError, match="absent from the loaded package"):
        compile_workload_plan(selection, package, "sat-a")
