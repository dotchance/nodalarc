# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Explicit workload selection for one deployment, completed before any write.

Reads the CR's selection pair, loads the package, resolves one profile per
node, compiles and composes every selected node — all without a single
Kubernetes API call. Any failure here is terminal for the selection: the
caller surfaces it and never falls back to the built-in FRR path.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from nodalarc.workloads.plan import compile_workload_plan
from nodalarc.workloads.refs import SelectionRef
from nodalarc.workloads.resolution import WorkloadSelection, resolve_node_workloads
from nodalarc.workloads.source import DirectoryPackageSource, LoadedPackage

from nodalarc_operator.workloads.compose import ComposedWorkload, compose_workload

log = logging.getLogger(__name__)

# The one explicitly named transitional producer: ONLY the built-in
# FRR-plus-observer profile receives the built-in renderer's per-node output
# as plan artifacts. Generic compilation and composition know nothing about
# FRR; this constant is the entire coupling surface, and it is removed when
# the profile-owned adapter renders natively.
TRANSITIONAL_FRR_OBSERVER_PROFILE = "nodalarc:profiles/frr/frr-observer.yaml"

# Built-in packages ship inside the operator image beside the FRR templates.
BUILTIN_PACKAGE_ROOT = Path("configs/workloads")

DEV_IMAGE_OVERRIDES_ENV = "WORKLOAD_DEV_IMAGE_OVERRIDES"


class WorkloadSelectionError(ValueError):
    """Terminal failure of an explicit selection. Never becomes FRR."""


@dataclass(frozen=True)
class SelectedWorkloads:
    """Everything an explicit deployment needs, computed before any write."""

    package: LoadedPackage
    selection: WorkloadSelection
    composed: dict[str, ComposedWorkload]

    @property
    def identity(self) -> str:
        """The one-line selection identity stamped on workload pods."""
        return f"{self.package.binding_ref}@{self.package.package_digest}"


def _dev_image_overrides() -> dict[str, str]:
    raw = os.environ.get(DEV_IMAGE_OVERRIDES_ENV, "").strip()
    if not raw:
        return {}
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkloadSelectionError(
            f"{DEV_IMAGE_OVERRIDES_ENV} is not valid JSON: {error}"
        ) from error
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise WorkloadSelectionError(
            f"{DEV_IMAGE_OVERRIDES_ENV} must map image references to image references"
        )
    return overrides


def _dev_override_pull_policy() -> str:
    """The configured pull policy, mandatory once overrides substitute images.

    Substituted references are mutable development tags; leaving Kubernetes'
    tag-derived default in place would silently pin a stale cached image.
    """
    pull_policy = os.environ.get("IMAGE_PULL_POLICY", "").strip()
    if not pull_policy:
        raise WorkloadSelectionError(
            f"IMAGE_PULL_POLICY must be set when {DEV_IMAGE_OVERRIDES_ENV} is active"
        )
    return pull_policy


def _apply_dev_image_overrides(
    composed: ComposedWorkload, overrides: dict[str, str], pull_policy: str
) -> None:
    """Explicit, visibly non-reproducible development substitution.

    Applied to composed Kubernetes containers only — admission and the
    package identity are untouched, so production digest policy is never
    weakened globally.
    """
    for container in (
        *composed.composition.init_containers,
        *composed.composition.containers,
    ):
        replacement = overrides.get(container.image)
        if replacement:
            log.warning(
                "DEV IMAGE OVERRIDE: container %r image %s replaced by %s — "
                "this deployment is not reproducible",
                container.name,
                container.image,
                replacement,
            )
            container.image = replacement
            container.image_pull_policy = pull_policy


def _transitional_plan_artifacts(
    profile_ref: str, node_id: str, rendered_configs: dict[str, dict[str, str]]
) -> dict[str, bytes]:
    if profile_ref != TRANSITIONAL_FRR_OBSERVER_PROFILE:
        return {}
    configs = rendered_configs.get(node_id)
    if not configs:
        raise WorkloadSelectionError(
            f"transitional FRR producer has no rendered configuration for {node_id!r}"
        )
    return {name: text.encode() for name, text in configs.items()}


def validate_workload_selection(
    selection_ref: SelectionRef | None,
    resolved_session,
    *,
    package_root: Path = BUILTIN_PACKAGE_ROOT,
) -> tuple[LoadedPackage, WorkloadSelection] | None:
    """Load, digest-verify, and resolve the selection without any side effect.

    This is the write-free deterministic gate: the reconciler runs it before
    mutating anything for a new generation. Returns None for the built-in
    FRR default path.
    Every failure raises WorkloadSelectionError — terminal, never FRR.
    """
    if selection_ref is None:
        return None

    try:
        package = DirectoryPackageSource(package_root).load(selection_ref.binding_ref)
    except ValueError as error:
        raise WorkloadSelectionError(f"selected package failed to load: {error}") from error

    if package.package_digest != selection_ref.package_digest:
        raise WorkloadSelectionError(
            f"loaded package digest {package.package_digest} does not match the "
            f"CR's desired digest {selection_ref.package_digest}"
        )

    try:
        selection = resolve_node_workloads(resolved_session, package)
    except ValueError as error:
        raise WorkloadSelectionError(f"binding resolution refused: {error}") from error
    return package, selection


def prepare_workload_selection(
    selection_ref: SelectionRef | None,
    resolved_session,
    rendered_configs: dict[str, dict[str, str]],
    *,
    namespace: str,
    owner_ref: dict,
    package_root: Path = BUILTIN_PACKAGE_ROOT,
) -> SelectedWorkloads | None:
    """Load, resolve, compile, and compose the entire explicit selection.

    Returns None when the CR carries no selection (built-in FRR default,
    unchanged).
    Every error raises WorkloadSelectionError: terminal for this selection,
    never a fallback.
    """
    validated = validate_workload_selection(
        selection_ref, resolved_session, package_root=package_root
    )
    if validated is None:
        return None
    package, selection = validated

    overrides = _dev_image_overrides()
    pull_policy = _dev_override_pull_policy() if overrides else ""
    composed: dict[str, ComposedWorkload] = {}
    for assignment in selection.assignments:
        artifacts = _transitional_plan_artifacts(
            str(assignment.profile_ref), assignment.node_id, rendered_configs
        )
        plan = compile_workload_plan(
            selection, package, assignment.node_id, plan_artifacts=artifacts
        )
        workload = compose_workload(plan, package, namespace=namespace, owner_ref=owner_ref)
        if overrides:
            _apply_dev_image_overrides(workload, overrides, pull_policy)
        composed[assignment.node_id] = workload

    log.info(
        "Explicit workload selection prepared: binding=%s digest=%s nodes=%d",
        package.binding_ref,
        package.package_digest,
        len(composed),
    )
    return SelectedWorkloads(package=package, selection=selection, composed=composed)
