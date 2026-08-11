# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Write-free workload preparation from the resolved session.

Every node's effective profile is a resolved fact; preparation admits each
referenced profile, renders per-node configuration through the profile's
adapter when it names one, and composes the Kubernetes data for every node.
No Kubernetes call happens here: the reconciler runs this before deleting or
reusing any pod, and every failure raises WorkloadPreparationError —
terminal, never a fallback.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.workloads.adapter import SessionContext
from nodalarc.workloads.admission import admit_profile
from nodalarc.workloads.plan import WorkloadPlan

from adapters.registry import adapter_named
from nodalarc_operator.workloads.compose import ComposedWorkload, compose_workload

log = logging.getLogger("nodalarc.operator.workloads")

DEV_IMAGE_OVERRIDES_ENV = "NODALARC_DEV_IMAGE_OVERRIDES"


class WorkloadPreparationError(ValueError):
    """Terminal failure preparing a session's workloads. Never a fallback."""


@dataclass(frozen=True)
class PreparedWorkloads:
    """Everything a deployment needs, computed before any write."""

    composed: dict[str, ComposedWorkload]
    # Content-addressed identity over every node's effective profile and
    # rendered configuration. Stamped on workload pods; a pod whose stamp
    # differs from the desired identity carries a different workload.
    identity: str


def _dev_image_overrides() -> dict[str, str]:
    raw = os.environ.get(DEV_IMAGE_OVERRIDES_ENV, "").strip()
    if not raw:
        return {}
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkloadPreparationError(
            f"{DEV_IMAGE_OVERRIDES_ENV} is not valid JSON: {error}"
        ) from error
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise WorkloadPreparationError(
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
        raise WorkloadPreparationError(
            f"IMAGE_PULL_POLICY must be set when {DEV_IMAGE_OVERRIDES_ENV} is active"
        )
    return pull_policy


def _apply_dev_image_overrides(
    composed: ComposedWorkload, overrides: dict[str, str], pull_policy: str
) -> None:
    """Explicit, visibly non-reproducible development substitution.

    Applied to composed Kubernetes containers only — admission and the
    profile identity are untouched, so production digest policy is never
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


def prepare_session_workloads(
    resolution,
    *,
    namespace: str,
    owner_ref: dict,
) -> PreparedWorkloads:
    """The COMPLETE write-free workload preparation for one resolved session.

    Admits every referenced profile, renders per-node configuration through
    each profile's adapter, and composes every node. The resolver already
    guaranteed each node an effective profile and gated adapter availability;
    absence of either here is a platform error, not a session error.
    """
    resolved = resolution.resolved
    unattached = sorted(
        node.node_id
        for node in resolved.nodes
        if node.forwarding == "host" and node.host_attachment is None
    )
    if unattached:
        raise WorkloadPreparationError(
            "host nodes without derivable attachment are not deployable "
            "(no LAN segment exposes them yet): " + ", ".join(unattached[:5])
        )

    admitted = {}
    for reference, profile in sorted(resolution.workload_profiles.items()):
        admission = admit_profile(
            {"profile": profile.model_dump(mode="json", exclude_none=True)},
            object_ref=reference,
        )
        if admission.profile is None:
            details = "; ".join(
                f"{evidence.code}: {evidence.detail}" for evidence in admission.rejections
            )
            raise WorkloadPreparationError(f"profile {reference} was not admitted: {details}")
        admitted[reference] = admission.profile

    context = SessionContext(resolved=resolved)
    overrides = _dev_image_overrides()
    pull_policy = _dev_override_pull_policy() if overrides else ""
    composed: dict[str, ComposedWorkload] = {}
    identity_payload: dict[str, dict] = {}
    for node in resolved.nodes:
        profile = admitted.get(node.profile)
        if profile is None:
            raise WorkloadPreparationError(
                f"node {node.node_id!r} references profile {node.profile!r}, absent "
                "from the resolution's admitted profiles"
            )
        adapter = adapter_named(profile.adapter)
        rendered: dict[str, bytes] = {}
        if adapter is not None:
            config = adapter.render_node(node, context)
            if config.env or config.args is not None:
                raise WorkloadPreparationError(
                    f"adapter {profile.adapter!r} produced env/args for "
                    f"{node.node_id!r}, but per-node env/args delivery is not yet "
                    "wired into composition"
                )
            rendered = dict(config.files)
        plan = WorkloadPlan(
            node_id=node.node_id,
            profile_ref=node.profile,
            rendered_files=rendered,
        )
        workload = compose_workload(plan, profile, namespace=namespace, owner_ref=owner_ref)
        if overrides:
            _apply_dev_image_overrides(workload, overrides, pull_policy)
        composed[node.node_id] = workload
        identity_payload[node.node_id] = {
            "profile": node.profile,
            "rendered": {name: sha256_digest(data) for name, data in sorted(rendered.items())},
        }

    identity = f"profiles@{sha256_digest(canonical_json_bytes(identity_payload))}"
    log.info(
        "Session workloads prepared: profiles=%d nodes=%d identity=%s",
        len(admitted),
        len(composed),
        identity,
    )
    return PreparedWorkloads(composed=composed, identity=identity)
