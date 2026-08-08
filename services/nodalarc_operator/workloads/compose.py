# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Translate an admitted workload profile and its plan into Kubernetes data.

Pure translation: one admitted profile plus one node's plan becomes a
WorkloadComposition and the session-owned artifact ConfigMap. No API calls
happen here — the existing reconciler creates every object and owns
create/409, ownership, and lifecycle. Nothing in this module knows any
provider: it reads only the admitted profile schema and the plan envelope.
"""

from __future__ import annotations

from dataclasses import dataclass

import kubernetes.client
from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.workloads.plan import WorkloadPlan
from nodalarc.workloads.profile import NodeWorkloadProfile, ProfileContainer
from nodalarc.workloads.source import LoadedPackage, LoadedProfile

from nodalarc_operator.workloads.materializer import WorkloadComposition

# Platform-owned volume names inside the composition. Profile volumes may
# not use them; the materializer separately reserves wiring-gate/status.
PLAN_ARTIFACT_VOLUME = "na-plan-artifacts"
STATIC_ARTIFACT_VOLUME = "na-static-artifacts"
_RESERVED_VOLUME_NAMES = frozenset({PLAN_ARTIFACT_VOLUME, STATIC_ARTIFACT_VOLUME})


@dataclass(frozen=True)
class ComposedWorkload:
    """The translator's complete output for one node."""

    composition: WorkloadComposition
    # Session-owned, content-addressed artifact ConfigMap (binaryData), or
    # None when the profile ships no artifacts. Created by the reconciler.
    artifact_config_map: kubernetes.client.V1ConfigMap | None


def _artifact_key(name: str) -> str:
    """ConfigMap keys cannot contain '/'; artifact delivery is flat today.

    Nested artifact trees get an explicit extension when a real profile
    needs one; inventing an encoding here would be a silent convention.
    """
    if "/" in name:
        raise ValueError(f"artifact name {name!r} is nested; artifact delivery is flat")
    return name


def _artifact_config_map(
    plan: WorkloadPlan,
    loaded: LoadedProfile,
    *,
    namespace: str,
    owner_ref: dict,
) -> kubernetes.client.V1ConfigMap | None:
    entries: dict[str, bytes] = {}
    for name, content in loaded.files.items():
        entries[f"static-{_artifact_key(name)}"] = content
    for name, content in plan.plan_artifacts.items():
        entries[f"plan-{_artifact_key(name)}"] = content
    if not entries:
        return None
    import base64

    content_id = sha256_digest(
        canonical_json_bytes(
            {
                "package": plan.package_digest,
                "entries": {key: sha256_digest(value) for key, value in sorted(entries.items())},
            }
        )
    )
    name = f"wl-{plan.node_id.lower()}-{content_id[len('sha256:') : len('sha256:') + 12]}"
    return kubernetes.client.V1ConfigMap(
        metadata=kubernetes.client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={
                "nodalarc.io/session": "true",
                "nodalarc.io/config-type": "workload-artifacts",
                "nodalarc.io/node-id": plan.node_id,
            },
            owner_references=[owner_ref],
        ),
        immutable=True,
        binary_data={
            key: base64.b64encode(value).decode() for key, value in sorted(entries.items())
        },
    )


def _security_context(container: ProfileContainer) -> kubernetes.client.V1SecurityContext:
    """The platform generates the effective security context; profiles only
    ever narrow it through admitted declarations."""
    return kubernetes.client.V1SecurityContext(
        capabilities=kubernetes.client.V1Capabilities(
            drop=["ALL"],
            add=list(container.capabilities) or None,
        ),
        read_only_root_filesystem=container.root_filesystem == "read_only",
        allow_privilege_escalation=False,
    )


def _resources(container: ProfileContainer) -> kubernetes.client.V1ResourceRequirements:
    requests = {
        "cpu": f"{container.resources.requests.cpu_m}m",
        "memory": f"{container.resources.requests.memory_mi}Mi",
    }
    limits = {
        "cpu": f"{container.resources.limits.cpu_m}m",
        "memory": f"{container.resources.limits.memory_mi}Mi",
    }
    ephemeral = container.resources.ephemeral_storage_mi
    if ephemeral is not None:
        requests["ephemeral-storage"] = f"{ephemeral.request}Mi"
        limits["ephemeral-storage"] = f"{ephemeral.limit}Mi"
    return kubernetes.client.V1ResourceRequirements(requests=requests, limits=limits)


def _container_mounts(
    container: ProfileContainer,
    profile: NodeWorkloadProfile,
    loaded: LoadedProfile,
    plan: WorkloadPlan,
) -> list[kubernetes.client.V1VolumeMount]:
    mounts = [
        kubernetes.client.V1VolumeMount(
            name=mount.volume, mount_path=mount.path, read_only=mount.read_only
        )
        for mount in container.volume_mounts
    ]
    for artifact in profile.artifacts.static:
        if artifact.container != container.name:
            continue
        mounts.append(
            kubernetes.client.V1VolumeMount(
                name=STATIC_ARTIFACT_VOLUME,
                mount_path=artifact.path,
                sub_path=f"static-{_artifact_key(artifact.file)}",
                read_only=True,
            )
        )
    plan_slot = profile.artifacts.plan
    if plan_slot is not None and plan_slot.container == container.name and plan.plan_artifacts:
        mounts.append(
            kubernetes.client.V1VolumeMount(
                name=PLAN_ARTIFACT_VOLUME,
                mount_path=plan_slot.path,
                read_only=True,
            )
        )
    return mounts


def _translate_container(
    container: ProfileContainer,
    profile: NodeWorkloadProfile,
    loaded: LoadedProfile,
    plan: WorkloadPlan,
) -> kubernetes.client.V1Container:
    readiness = profile.readiness
    probe = None
    if readiness is not None and readiness.container == container.name:
        probe = kubernetes.client.V1Probe(
            _exec=kubernetes.client.V1ExecAction(command=list(readiness.argv)),
            period_seconds=readiness.period_seconds,
            timeout_seconds=readiness.timeout_seconds,
        )
    return kubernetes.client.V1Container(
        name=container.name,
        image=container.image,
        command=list(container.command) if container.command is not None else None,
        args=list(container.args) if container.args is not None else None,
        security_context=_security_context(container),
        resources=_resources(container),
        readiness_probe=probe,
        volume_mounts=_container_mounts(container, profile, loaded, plan) or None,
    )


def compose_workload(
    plan: WorkloadPlan,
    package: LoadedPackage,
    *,
    namespace: str,
    owner_ref: dict,
) -> ComposedWorkload:
    """Translate one node's plan into composition data and owned objects."""
    if plan.package_digest != package.package_digest:
        raise ValueError(
            f"plan was compiled from package {plan.package_digest}, "
            f"not the loaded package {package.package_digest}"
        )
    loaded = package.profiles.get(str(plan.profile_ref))
    if loaded is None:
        raise ValueError(f"plan names profile {plan.profile_ref} absent from the loaded package")
    profile = loaded.profile

    reserved = _RESERVED_VOLUME_NAMES & {volume.name for volume in profile.volumes}
    if reserved:
        raise ValueError(f"profile volumes may not use platform names: {sorted(reserved)}")
    if plan.plan_artifacts and profile.artifacts.plan is None:
        raise ValueError(f"profile {plan.profile_ref} declares no plan-artifact destination")

    artifact_cm = _artifact_config_map(plan, loaded, namespace=namespace, owner_ref=owner_ref)

    volumes = [
        kubernetes.client.V1Volume(
            name=volume.name,
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(
                medium="Memory" if volume.medium == "memory" else None,
                size_limit=f"{volume.size_mi}Mi",
            ),
        )
        for volume in profile.volumes
    ]
    if artifact_cm is not None:
        cm_name = artifact_cm.metadata.name
        if loaded.files:
            volumes.append(
                kubernetes.client.V1Volume(
                    name=STATIC_ARTIFACT_VOLUME,
                    config_map=kubernetes.client.V1ConfigMapVolumeSource(name=cm_name),
                )
            )
        if plan.plan_artifacts:
            volumes.append(
                kubernetes.client.V1Volume(
                    name=PLAN_ARTIFACT_VOLUME,
                    config_map=kubernetes.client.V1ConfigMapVolumeSource(
                        name=cm_name,
                        items=[
                            kubernetes.client.V1KeyToPath(
                                key=f"plan-{_artifact_key(name)}", path=name
                            )
                            for name in sorted(plan.plan_artifacts)
                        ],
                    ),
                )
            )

    composition = WorkloadComposition(
        containers=[
            _translate_container(container, profile, loaded, plan)
            for container in profile.workload_containers
        ],
        volumes=volumes,
        init_containers=[
            _translate_container(container, profile, loaded, plan)
            for container in profile.init_containers
        ],
    )
    return ComposedWorkload(composition=composition, artifact_config_map=artifact_cm)
