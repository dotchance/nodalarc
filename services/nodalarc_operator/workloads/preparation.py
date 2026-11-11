# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Write-free workload preparation from the resolved session.

Every node's effective profile is a resolved fact; preparation admits each
referenced profile, renders per-node configuration through the profile's
adapter when it names one, and composes the Kubernetes data for every node.
No Kubernetes call happens here: the reconciler runs this before deleting or
reusing any pod, and every failure raises WorkloadPreparationError —
terminal, never a fallback.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.models.catalog import LiteralEnvEntry
from nodalarc.resolve_session import SessionResolutionError, resolve_env_value
from nodalarc.workloads.adapter import AdapterRenderRefusal, SessionContext
from nodalarc.workloads.admission import admit_profile
from nodalarc.workloads.plan import WorkloadPlan

from adapters.registry import adapter_named
from nodalarc_operator.workloads.compose import ComposedWorkload, compose_workload
from nodalarc_operator.workloads.images import (
    WORKLOAD_IMAGES_ENV,
    WorkloadImageMap,
    WorkloadImageMapError,
    is_logical_reference,
    load_workload_image_map,
)
from nodalarc_operator.workloads.materializer import image_pull_policy_for

log = logging.getLogger("nodalarc.operator.workloads")


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


def _image_map_pull_policy() -> str:
    """The configured pull policy, mandatory once the map resolves images.

    A developer build is named by a mutable tag; leaving Kubernetes'
    tag-derived default in place would silently run a stale cached image.
    """
    pull_policy = os.environ.get("IMAGE_PULL_POLICY", "").strip()
    if not pull_policy:
        raise WorkloadPreparationError(
            f"IMAGE_PULL_POLICY must be set when {WORKLOAD_IMAGES_ENV} names images"
        )
    return pull_policy


def _resolve_logical_images(
    composed: ComposedWorkload,
    image_map: WorkloadImageMap,
    pull_policy: str,
    *,
    node_id: str,
    profile_ref: str,
) -> list[str]:
    """Replace every logical image with the image the map names.

    A logical image the map does not name is refused. Returns the image of
    every container, in order, for the workload identity.
    """
    images: list[str] = []
    for container in (
        *composed.composition.init_containers,
        *composed.composition.containers,
    ):
        if is_logical_reference(container.image):
            actual = image_map.images.get(container.image)
            if actual is None:
                raise WorkloadPreparationError(
                    f"node {node_id!r}: profile {profile_ref} names {container.image}, which "
                    f"the workload image map ({WORKLOAD_IMAGES_ENV}) does not resolve"
                )
            container.image = actual
            container.image_pull_policy = image_pull_policy_for(actual, pull_policy)
        images.append(container.image)
    return images


def _resolved_env(entries, nodes) -> dict[str, str]:
    """Compute one container's final environment: literals plus resolved facts.

    The resolver already validated every reference; a failure here is a
    platform error, never a session error.
    """
    values: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, LiteralEnvEntry):
            values[entry.name] = entry.value
            continue
        try:
            values[entry.name] = resolve_env_value(entry.value_from, nodes)
        except SessionResolutionError as error:
            raise WorkloadPreparationError(
                f"env {entry.name!r} passed resolution but failed preparation: {error}"
            ) from error
    return values


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
    profile_content: dict[str, str] = {}
    profile_env: dict[str, tuple[dict[str, str], dict[str, dict[str, str]]]] = {}
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
        profile_content[reference] = sha256_digest(
            canonical_json_bytes(admission.profile.model_dump(mode="json"))
        )
        profile_env[reference] = (
            _resolved_env(admission.profile.env, resolved.nodes),
            {
                sidecar.name: _resolved_env(sidecar.env, resolved.nodes)
                for sidecar in admission.profile.sidecars
                if sidecar.env
            },
        )

    context = SessionContext(resolved=resolved)
    try:
        image_map = load_workload_image_map()
    except WorkloadImageMapError as error:
        raise WorkloadPreparationError(str(error)) from error
    pull_policy = _image_map_pull_policy() if image_map.images else ""
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
            try:
                config = adapter.render_node(node, context)
            except AdapterRenderRefusal as exc:
                raise WorkloadPreparationError(
                    f"adapter {profile.adapter!r} cannot render {node.node_id!r}: {exc}"
                ) from exc
            except Exception as exc:
                # render_node is pure, so an exception it raises recurs on
                # every attempt: the session fails with it.
                raise WorkloadPreparationError(
                    f"adapter {profile.adapter!r} failed to render {node.node_id!r}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if config.env or config.args is not None:
                raise WorkloadPreparationError(
                    f"adapter {profile.adapter!r} produced env/args for "
                    f"{node.node_id!r}, but per-node env/args delivery is not yet "
                    "wired into composition"
                )
            rendered = dict(config.files)
        primary_env, sidecar_env = profile_env[node.profile]
        plan = WorkloadPlan(
            node_id=node.node_id,
            profile_ref=node.profile,
            rendered_files=rendered,
            env=primary_env,
            sidecar_env=sidecar_env,
        )
        workload = compose_workload(plan, profile, namespace=namespace, owner_ref=owner_ref)
        images = _resolve_logical_images(
            workload, image_map, pull_policy, node_id=node.node_id, profile_ref=node.profile
        )
        composed[node.node_id] = workload
        identity_payload[node.node_id] = {
            "profile": node.profile,
            "profile_content": profile_content[node.profile],
            "env": {
                "primary": dict(sorted(primary_env.items())),
                "sidecars": {
                    name: dict(sorted(env.items())) for name, env in sorted(sidecar_env.items())
                },
            },
            "rendered": {name: sha256_digest(data) for name, data in sorted(rendered.items())},
            # The images that run: a new image under an unchanged profile is a
            # different workload, and its pods are replaced.
            "images": images,
        }

    identity = f"profiles@{sha256_digest(canonical_json_bytes(identity_payload))}"
    log.info(
        "Session workloads prepared: profiles=%d nodes=%d identity=%s",
        len(admitted),
        len(composed),
        identity,
    )
    return PreparedWorkloads(composed=composed, identity=identity)
