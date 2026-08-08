# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload plan: one node's resolved selection, as a small envelope.

A plan names the node, the selected profile, and the one package identity,
and carries any pre-rendered per-node artifacts (exact bytes) an adapter
produced for this node. It is deliberately NOT a second copy of the
profile's container, volume, resource, or readiness schema — consumers look
the profile up in the loaded package by reference and read the same
admitted model everyone else reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from nodalarc.content_identity import Sha256Digest
from nodalarc.workloads.profile import validate_package_relative_path
from nodalarc.workloads.refs import ProfileRef
from nodalarc.workloads.resolution import WorkloadSelection
from nodalarc.workloads.source import LoadedPackage


@dataclass(frozen=True, slots=True)
class WorkloadPlan:
    """One node bound to one profile under one package identity."""

    node_id: str
    profile_ref: ProfileRef
    package_digest: Sha256Digest
    # Pre-rendered per-node artifacts (for example a rendered native
    # configuration set), mount-relative file name -> exact bytes. Projected
    # by the materializer path; never interpreted by it.
    plan_artifacts: Mapping[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("plan node_id must be non-empty")
        if not isinstance(self.profile_ref, ProfileRef):
            raise TypeError("profile_ref must be a ProfileRef")
        frozen = MappingProxyType(dict(self.plan_artifacts))
        object.__setattr__(self, "plan_artifacts", frozen)
        for name, content in frozen.items():
            validate_package_relative_path(name)
            if not isinstance(content, bytes):
                raise TypeError(f"plan artifact {name!r} must be bytes")


def compile_workload_plan(
    selection: WorkloadSelection,
    package: LoadedPackage,
    node_id: str,
    *,
    plan_artifacts: Mapping[str, bytes] | None = None,
) -> WorkloadPlan:
    """Compile one node of a selection against the exact package it resolved.

    Selection and package must name the same binding and the same package
    digest — an assignment resolved from one package can never be compiled
    against another that happens to share a profile reference. The node
    must belong to this selection, and plan artifacts are accepted only
    when the selected profile declares a delivery slot for them; the
    platform never invents a destination.
    """
    if selection.binding_ref != package.binding_ref:
        raise ValueError(
            f"selection binding {selection.binding_ref} does not match "
            f"loaded package binding {package.binding_ref}"
        )
    if selection.package_digest != package.package_digest:
        raise ValueError(
            f"selection was resolved from package {selection.package_digest}, "
            f"not the loaded package {package.package_digest}"
        )
    assignment = next((entry for entry in selection.assignments if entry.node_id == node_id), None)
    if assignment is None:
        raise ValueError(f"node {node_id!r} does not belong to this selection")
    loaded = package.profiles.get(str(assignment.profile_ref))
    if loaded is None:
        raise ValueError(
            f"selection assigns profile {assignment.profile_ref} absent from the loaded package"
        )
    artifacts = dict(plan_artifacts or {})
    if artifacts and loaded.profile.artifacts.plan is None:
        raise ValueError(
            f"profile {assignment.profile_ref} declares no plan-artifact "
            "destination; refusing artifacts with nowhere to go"
        )
    return WorkloadPlan(
        node_id=assignment.node_id,
        profile_ref=assignment.profile_ref,
        package_digest=package.package_digest,
        plan_artifacts=artifacts,
    )
