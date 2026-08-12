# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Translate an admitted catalog profile and its plan into Kubernetes data.

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
from nodalarc.models.catalog import Profile, ProfileSidecar
from nodalarc.workloads.plan import WorkloadPlan

from nodalarc_operator.workloads.materializer import WorkloadComposition

# Platform-owned volume names inside the composition. Profile volumes may
# not use them; the materializer separately reserves wiring-gate/status.
PLAN_ARTIFACT_VOLUME = "na-plan-artifacts"
TERMINAL_KEYS_VOLUME = "na-terminal-keys"
TERMINAL_KEYS_SECRET = "nodalarc-terminal-keys"
_RESERVED_VOLUME_NAMES = frozenset({PLAN_ARTIFACT_VOLUME, TERMINAL_KEYS_VOLUME})


@dataclass(frozen=True)
class ComposedWorkload:
    """The translator's complete output for one node."""

    composition: WorkloadComposition
    # Session-owned, content-addressed artifact ConfigMap (binaryData), or
    # None when the plan carries no rendered files. Created by the reconciler.
    artifact_config_map: kubernetes.client.V1ConfigMap | None
    # The pod's terminal contract as canonical JSON, or None when the
    # profile declines terminal access.
    terminal_access: str | None = None


def _artifact_key(name: str) -> str:
    """Deterministic opaque ConfigMap key for a rendered file name."""

    digest = sha256_digest(name.encode())
    return f"p-{digest[len('sha256:') : len('sha256:') + 16]}"


def _artifact_config_map(
    plan: WorkloadPlan,
    *,
    namespace: str,
    owner_ref: dict,
) -> kubernetes.client.V1ConfigMap | None:
    if not plan.rendered_files:
        return None
    import base64

    entries = {_artifact_key(name): content for name, content in plan.rendered_files.items()}
    content_id = sha256_digest(
        canonical_json_bytes(
            {
                "profile": plan.profile_ref,
                "entries": {key: sha256_digest(value) for key, value in sorted(entries.items())},
            }
        )
    )
    # The name carries the owning CR incarnation alongside the content
    # identity: identical bytes under a replaced CR must never collide with
    # an old CR's immutable object.
    owner_uid = str(owner_ref.get("uid") or "")
    if not owner_uid:
        raise ValueError("artifact ConfigMap requires an owner UID")
    incarnation = owner_uid.replace("-", "")[:8].lower()
    name = (
        f"wl-{plan.node_id.lower()}-{incarnation}-"
        f"{content_id[len('sha256:') : len('sha256:') + 12]}"
    )
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


def _security_context(
    capabilities: tuple[str, ...],
    root_filesystem: str,
) -> kubernetes.client.V1SecurityContext:
    """The platform generates the effective security context; profiles only
    ever narrow it through admitted declarations."""

    return kubernetes.client.V1SecurityContext(
        capabilities=kubernetes.client.V1Capabilities(
            drop=["ALL"],
            add=list(capabilities) or None,
        ),
        read_only_root_filesystem=root_filesystem == "read_only",
        allow_privilege_escalation=False,
    )


def _resources(resources) -> kubernetes.client.V1ResourceRequirements:
    return kubernetes.client.V1ResourceRequirements(
        requests={
            "cpu": f"{resources.requests.cpu_m}m",
            "memory": f"{resources.requests.memory_mi}Mi",
        },
        limits={
            "cpu": f"{resources.limits.cpu_m}m",
            "memory": f"{resources.limits.memory_mi}Mi",
        },
    )


def _pull_reference(registry: str, image: str) -> str:
    return f"{registry}/{image}"


def _env_list(values) -> list[kubernetes.client.V1EnvVar] | None:
    return [
        kubernetes.client.V1EnvVar(name=name, value=value) for name, value in sorted(values.items())
    ] or None


def _primary_container(
    profile: Profile,
    plan: WorkloadPlan,
) -> kubernetes.client.V1Container:
    mounts = [
        kubernetes.client.V1VolumeMount(
            name=mount.volume, mount_path=mount.path, read_only=mount.read_only
        )
        for mount in profile.mounts
    ]
    if profile.config_mount is not None and plan.rendered_files:
        mounts.append(
            kubernetes.client.V1VolumeMount(
                name=PLAN_ARTIFACT_VOLUME,
                mount_path=profile.config_mount,
                read_only=True,
            )
        )
    if profile.terminal is not None and profile.terminal.surface == "ssh":
        mounts.append(
            kubernetes.client.V1VolumeMount(
                name=TERMINAL_KEYS_VOLUME,
                mount_path=profile.terminal.authorized_keys_path,
                read_only=True,
            )
        )
    probe = None
    if profile.readiness is not None:
        probe = kubernetes.client.V1Probe(
            _exec=kubernetes.client.V1ExecAction(command=list(profile.readiness.argv)),
            period_seconds=profile.readiness.period_seconds,
            timeout_seconds=profile.readiness.timeout_seconds,
        )
    return kubernetes.client.V1Container(
        name=profile.id,
        image=_pull_reference(profile.registry, profile.image),
        command=list(profile.command) if profile.command is not None else None,
        args=list(profile.args) if profile.args is not None else None,
        env=_env_list(plan.env),
        security_context=_security_context(profile.capabilities, profile.root_filesystem),
        resources=_resources(profile.resources),
        readiness_probe=probe,
        volume_mounts=mounts or None,
    )


def _sidecar_container(
    sidecar: ProfileSidecar,
    profile: Profile,
    plan: WorkloadPlan,
) -> kubernetes.client.V1Container:
    mounts = [
        kubernetes.client.V1VolumeMount(
            name=mount.volume, mount_path=mount.path, read_only=mount.read_only
        )
        for mount in sidecar.mounts
    ]
    return kubernetes.client.V1Container(
        name=sidecar.name,
        image=_pull_reference(sidecar.registry or profile.registry, sidecar.image),
        command=list(sidecar.command) if sidecar.command is not None else None,
        args=list(sidecar.args) if sidecar.args is not None else None,
        env=_env_list(plan.sidecar_env.get(sidecar.name, {})),
        security_context=_security_context(sidecar.capabilities, sidecar.root_filesystem),
        resources=_resources(sidecar.resources),
        volume_mounts=mounts or None,
    )


def compose_workload(
    plan: WorkloadPlan,
    profile: Profile,
    *,
    namespace: str,
    owner_ref: dict,
) -> ComposedWorkload:
    """Translate one node's plan and admitted profile into composition data."""

    reserved = _RESERVED_VOLUME_NAMES & {volume.name for volume in profile.volumes}
    if reserved:
        raise ValueError(f"profile volumes may not use platform names: {sorted(reserved)}")
    if plan.rendered_files and profile.config_mount is None:
        raise ValueError(f"profile {plan.profile_ref} declares no config_mount for rendered files")

    artifact_cm = _artifact_config_map(plan, namespace=namespace, owner_ref=owner_ref)

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
        volumes.append(
            kubernetes.client.V1Volume(
                name=PLAN_ARTIFACT_VOLUME,
                config_map=kubernetes.client.V1ConfigMapVolumeSource(
                    name=artifact_cm.metadata.name,
                    items=[
                        kubernetes.client.V1KeyToPath(key=_artifact_key(name), path=name)
                        for name in sorted(plan.rendered_files)
                    ],
                ),
            )
        )

    terminal_access: str | None = None
    if profile.terminal is not None:
        if profile.terminal.surface == "ssh":
            # The platform owns key lifecycle: mount the session public key
            # where the profile's SSH daemon reads it.
            volumes.append(
                kubernetes.client.V1Volume(
                    name=TERMINAL_KEYS_VOLUME,
                    secret=kubernetes.client.V1SecretVolumeSource(
                        secret_name=TERMINAL_KEYS_SECRET,
                        items=[
                            kubernetes.client.V1KeyToPath(
                                key="id_ed25519.pub", path="authorized_keys"
                            )
                        ],
                    ),
                )
            )
            terminal_access = canonical_json_bytes({"surface": "ssh"}).decode()
        else:
            terminal_access = canonical_json_bytes(
                {
                    "surface": "exec",
                    "container": profile.id,
                    "command": list(profile.terminal.command or ()),
                }
            ).decode()

    composition = WorkloadComposition(
        containers=[
            _primary_container(profile, plan),
            *(_sidecar_container(sidecar, profile, plan) for sidecar in profile.sidecars),
        ],
        volumes=volumes,
        init_containers=[],
    )
    return ComposedWorkload(
        composition=composition,
        artifact_config_map=artifact_cm,
        terminal_access=terminal_access,
    )
