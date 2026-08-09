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

from nodalarc.workloads.adapter import SessionContext
from nodalarc.workloads.plan import compile_workload_plan
from nodalarc.workloads.refs import SelectionRef
from nodalarc.workloads.resolution import WorkloadSelection, resolve_node_workloads
from nodalarc.workloads.source import DirectoryPackageSource, LoadedPackage

from adapters.registry import adapter_for
from nodalarc_operator.workloads.compose import ComposedWorkload, compose_workload

log = logging.getLogger(__name__)

# Built-in bindings ship inside the operator image; the profiles they reference
# live in the adapter trees the operator also carries.
BUILTIN_PACKAGE_ROOT = Path("configs/workloads")
BUILTIN_PROFILES_ROOT = Path("adapters")

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


def _adapter_plan_artifacts(
    profile_ref: str, node_id: str, context: SessionContext
) -> dict[str, bytes]:
    """Render one node's native config through its adapter, as plan artifacts.

    A profile with no registered adapter contributes no artifacts: its
    containers are fully self-describing. An adapter that returns environment
    or arguments fails loudly here — per-node env/args delivery is not yet
    wired into composition, and silently dropping it would misrepresent the
    node's configuration.
    """
    adapter = adapter_for(profile_ref)
    if adapter is None:
        return {}
    node = context.resolved.node_by_id(node_id)
    if node is None:
        raise WorkloadSelectionError(
            f"selection assigns node {node_id!r}, absent from the resolved session"
        )
    rendered = adapter.render_node(node, context)
    if rendered.env or rendered.args is not None:
        raise WorkloadSelectionError(
            f"adapter for {profile_ref} produced env/args for {node_id!r}, but per-node "
            "env/args delivery is not yet wired into composition"
        )
    return dict(rendered.files)


def validate_workload_selection(
    selection_ref: SelectionRef | None,
    resolved_session,
    *,
    package_root: Path = BUILTIN_PACKAGE_ROOT,
    profiles_root: Path | None = None,
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
        package = DirectoryPackageSource(package_root, profiles_root=profiles_root).load(
            selection_ref.binding_ref
        )
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
    *,
    namespace: str,
    owner_ref: dict,
    package_root: Path = BUILTIN_PACKAGE_ROOT,
    profiles_root: Path | None = None,
) -> SelectedWorkloads | None:
    """Load, resolve, compile, and compose the entire explicit selection.

    Each node's native configuration is rendered through its adapter and
    delivered as plan artifacts. Returns None when the CR carries no selection
    (built-in FRR default, unchanged).
    Every error raises WorkloadSelectionError: terminal for this selection,
    never a fallback.
    """
    validated = validate_workload_selection(
        selection_ref, resolved_session, package_root=package_root, profiles_root=profiles_root
    )
    if validated is None:
        return None
    package, selection = validated

    context = SessionContext(resolved=resolved_session)
    overrides = _dev_image_overrides()
    pull_policy = _dev_override_pull_policy() if overrides else ""
    composed: dict[str, ComposedWorkload] = {}
    for assignment in selection.assignments:
        artifacts = _adapter_plan_artifacts(
            str(assignment.profile_ref), assignment.node_id, context
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
