# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The one shared Pod assembly for session workloads.

Every session pod is assembled here and only here, from its node's admitted
profile composition. The materializer owns
what the platform owns: pod identity and labels, CR ownership, node pinning,
the wiring gate that holds authored containers until this exact pod
incarnation is wired, token and DNS policy, and the restart policy. It
contains no provider or protocol branches: a composition hands in
containers, volumes, and init containers as data, and the assembly never
inspects them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import kubernetes.client
from nodalarc.substrate.manifest_contract import (
    POD_OWNER_UID_LABEL,
    POD_SESSION_RUN_LABEL,
)
from nodalarc.substrate.wiring_status import (
    READY_PHASE_JQ_CLAUSE,
    WIRING_STATUS_FILE,
    WIRING_STATUS_VOLUME,
)
from nodalarc.workload_target import (
    NODE_ID_LABEL,
    PRIMARY_CONTAINER_ANNOTATION,
    TERMINAL_ACCESS_ANNOTATION,
)

SESSION_LABEL = "nodalarc.io/session"
ROLE_LABEL = "nodalarc.io/role"

# Platform-owned pod annotation carrying the built-in-or-explicit workload
# selection identity. The reconciler compares it against the CR's current
# selection; a differing pod is deleted and recreated, never re-stamped.
# One configuration for every client model this Operator builds. A model
# constructed without one builds its own Configuration, which reconfigures the
# client's loggers each time; a session pod takes dozens of models.
MODEL_CONFIGURATION = kubernetes.client.Configuration()

WORKLOAD_SELECTION_ANNOTATION = "nodalarc.io/workload-selection"

TERMINAL_SSH_CONTRACT = '{"surface":"ssh"}'

# The gate runs its loop as a child of a PID 1 that exits on SIGTERM: the kernel
# does not deliver a signal to a PID namespace's init process unless it has a
# handler, and a pod deleted during wiring would otherwise wait out its whole
# termination grace period. The loop's own exit status is the gate's.
_WIRING_GATE_SCRIPT = (
    "trap 'exit 143' TERM\n"
    "{\n"
    'my_netns="$(readlink /proc/self/ns/net)"\n'
    'my_netns="${my_netns#net:[}"\n'
    'my_netns="${my_netns%]}"\n'
    f'status_file="/{WIRING_STATUS_VOLUME}/{WIRING_STATUS_FILE}"\n'
    'echo "waiting for platform wiring of ${NODE_ID} '
    '(pod ${POD_UID}, run ${SESSION_RUN_ID}, netns ${my_netns})"\n'
    'last=""\n'
    "while true; do\n"
    # The Node Agent replaces the file (write, then rename) when it writes
    # the proof. Reading it is a shell builtin; jq runs only when the
    # content changed.
    '  current=""\n'
    '  [ -f "${status_file}" ] && read -r -d "" current < "${status_file}"\n'
    '  if [ -n "${current}" ] && [ "${current}" != "${last}" ]; then\n'
    '    last="${current}"\n'
    '    if jq -e --arg uid "${POD_UID}" '
    '--arg run "${SESSION_RUN_ID}" --arg ns "${my_netns}" '
    '\'.status == "ready" and .dirty_kernel == false '
    "and .pod_uid == $uid and .session_run_id == $run "
    "and .netns_id == $ns "
    f"and {READY_PHASE_JQ_CLAUSE}' "
    '"${status_file}" > /dev/null 2>&1; then\n'
    '      echo "wiring ready for ${NODE_ID}"\n'
    "      exit 0\n"
    "    fi\n"
    "  fi\n"
    "  sleep 0.2\n"
    "done\n"
    "} & wait $!\n"
)


def image_pull_policy_for(image: str, configured: str) -> str:
    """The pull policy for one container image under the platform's configured policy.

    A reference that carries a digest names immutable content: a node that
    holds it has exactly that image, so ``Always`` becomes ``IfNotPresent``
    and the kubelet does not ask the registry again. A session start creates
    hundreds of containers at once, and their pulls of present images queue
    behind one another (measured about 50 ms each, 8.9 s for 180 on one
    node). A tag reference, and every other configured policy, is kept.
    """
    if configured == "Always" and "@sha256:" in image:
        return "IfNotPresent"
    return configured


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


@dataclass(frozen=True)
class WorkloadComposition:
    """Authored container composition for one session pod, handed in as data."""

    containers: list[kubernetes.client.V1Container]
    volumes: list[kubernetes.client.V1Volume]
    # The container running the node's primary workload, named explicitly so
    # no consumer infers it from position. Published on the pod.
    primary_container: str
    init_containers: list[kubernetes.client.V1Container] = field(default_factory=list)


def _wiring_gate_container() -> kubernetes.client.V1Container:
    # Platform wiring gate: authored containers start only after the Node
    # Agent has wired THIS pod incarnation. The gate observes the wiring proof
    # the Node Agent wrote for this pod (the same proof as the pod's
    # annotation, written into the pod's wiring-status volume) and exits when
    # the proof reports ready with a clean kernel AND names this exact
    # incarnation and run: pod UID (downward API), session run label, and the inode of
    # the network namespace the gate itself runs in. A row written for a
    # replaced pod, a recreated sandbox, or a previous run can never
    # release the workload. The gate never times out: wiring that does not
    # complete must surface as a pod stuck in Init, not as a workload
    # started on an unwired network.
    return kubernetes.client.V1Container(
        local_vars_configuration=MODEL_CONFIGURATION,
        name="wiring-gate",
        image=_require_env("WIRING_GATE_IMAGE"),
        image_pull_policy=image_pull_policy_for(
            _require_env("WIRING_GATE_IMAGE"), _require_env("IMAGE_PULL_POLICY")
        ),
        command=["bash", "-c", _WIRING_GATE_SCRIPT],
        env=[
            kubernetes.client.V1EnvVar(
                local_vars_configuration=MODEL_CONFIGURATION,
                name="NODE_ID",
                value_from=kubernetes.client.V1EnvVarSource(
                    local_vars_configuration=MODEL_CONFIGURATION,
                    field_ref=kubernetes.client.V1ObjectFieldSelector(
                        local_vars_configuration=MODEL_CONFIGURATION,
                        field_path=f"metadata.labels['{NODE_ID_LABEL}']",
                    ),
                ),
            ),
            kubernetes.client.V1EnvVar(
                local_vars_configuration=MODEL_CONFIGURATION,
                name="POD_UID",
                value_from=kubernetes.client.V1EnvVarSource(
                    local_vars_configuration=MODEL_CONFIGURATION,
                    field_ref=kubernetes.client.V1ObjectFieldSelector(
                        local_vars_configuration=MODEL_CONFIGURATION, field_path="metadata.uid"
                    ),
                ),
            ),
            kubernetes.client.V1EnvVar(
                local_vars_configuration=MODEL_CONFIGURATION,
                name="SESSION_RUN_ID",
                value_from=kubernetes.client.V1EnvVarSource(
                    local_vars_configuration=MODEL_CONFIGURATION,
                    field_ref=kubernetes.client.V1ObjectFieldSelector(
                        local_vars_configuration=MODEL_CONFIGURATION,
                        field_path=f"metadata.labels['{POD_SESSION_RUN_LABEL}']",
                    ),
                ),
            ),
        ],
        security_context=kubernetes.client.V1SecurityContext(
            local_vars_configuration=MODEL_CONFIGURATION,
            capabilities=kubernetes.client.V1Capabilities(
                local_vars_configuration=MODEL_CONFIGURATION, drop=["ALL"]
            ),
            read_only_root_filesystem=True,
            allow_privilege_escalation=False,
        ),
        resources=kubernetes.client.V1ResourceRequirements(
            local_vars_configuration=MODEL_CONFIGURATION,
            requests={"memory": "16Mi", "cpu": "10m"},
            limits={"memory": "32Mi", "cpu": "100m"},
        ),
        volume_mounts=[
            kubernetes.client.V1VolumeMount(
                local_vars_configuration=MODEL_CONFIGURATION,
                name=WIRING_STATUS_VOLUME,
                mount_path=f"/{WIRING_STATUS_VOLUME}",
                read_only=True,
            ),
        ],
    )


def _wiring_status_volume() -> kubernetes.client.V1Volume:
    """The directory the Node Agent writes this pod's wiring proof into.

    A disk-backed emptyDir: the kubelet creates it on the host before the
    pod sandbox, and the Node Agent writes the proof file there from the
    host side the moment it writes the proof annotation. The pod is
    creatable before any proof exists, when the directory is empty.
    """
    return kubernetes.client.V1Volume(
        local_vars_configuration=MODEL_CONFIGURATION,
        name=WIRING_STATUS_VOLUME,
        empty_dir=kubernetes.client.V1EmptyDirVolumeSource(
            local_vars_configuration=MODEL_CONFIGURATION, size_limit="1Mi"
        ),
    )


def build_session_pod(
    *,
    pod_name: str,
    namespace: str,
    node_id: str,
    role: str,
    session_id: str,
    owner_ref: dict,
    composition: WorkloadComposition,
    selection_identity: str,
    terminal_access: str | None = None,
    target_node: str | None = None,
    extra_labels: dict[str, str] | None = None,
) -> kubernetes.client.V1Pod:
    """Assemble one session pod from platform identity and a composition.

    Platform identity and platform names are not composable surface: extra
    labels may not overlap the identity label set, and no authored
    container or volume may use the reserved wiring-gate/wiring-status
    names. Every pod carries its built-in-or-explicit selection identity as a
    platform-owned annotation; the reconciler never adopts a pod whose
    annotation differs from the CR's current selection.
    """
    if not selection_identity:
        raise ValueError("selection_identity is required on every session pod")
    labels: dict[str, str] = {
        SESSION_LABEL: "true",
        NODE_ID_LABEL: node_id,
        ROLE_LABEL: role,
        POD_SESSION_RUN_LABEL: session_id,
        POD_OWNER_UID_LABEL: str(owner_ref.get("uid") or ""),
    }
    if extra_labels:
        overlap = sorted(set(extra_labels) & set(labels))
        if overlap:
            raise ValueError(f"extra_labels may not override platform identity labels: {overlap}")
        labels.update(extra_labels)

    reserved_containers = sorted(
        container.name
        for container in (*composition.init_containers, *composition.containers)
        if container.name == "wiring-gate"
    )
    if reserved_containers:
        raise ValueError("composition may not use the reserved container name 'wiring-gate'")
    if any(volume.name == WIRING_STATUS_VOLUME for volume in composition.volumes):
        raise ValueError(
            f"composition may not use the reserved volume name {WIRING_STATUS_VOLUME!r}"
        )
    declared = [container.name for container in composition.containers]
    if composition.primary_container not in declared:
        raise ValueError(
            f"composition names primary container {composition.primary_container!r} "
            f"but declares {declared}"
        )

    return kubernetes.client.V1Pod(
        local_vars_configuration=MODEL_CONFIGURATION,
        metadata=kubernetes.client.V1ObjectMeta(
            local_vars_configuration=MODEL_CONFIGURATION,
            name=pod_name,
            namespace=namespace,
            labels=labels,
            annotations={
                WORKLOAD_SELECTION_ANNOTATION: selection_identity,
                PRIMARY_CONTAINER_ANNOTATION: composition.primary_container,
                **(
                    {TERMINAL_ACCESS_ANNOTATION: terminal_access}
                    if terminal_access is not None
                    else {}
                ),
            },
            owner_references=[owner_ref],
        ),
        spec=kubernetes.client.V1PodSpec(
            local_vars_configuration=MODEL_CONFIGURATION,
            node_name=target_node,
            init_containers=[_wiring_gate_container(), *composition.init_containers],
            containers=list(composition.containers),
            volumes=[*composition.volumes, _wiring_status_volume()],
            restart_policy="Never",
            automount_service_account_token=False,
            # Fast DNS timeout: pod IPs have no PTR records in CoreDNS.
            # Without this, every reverse DNS lookup (traceroute hops, sshd
            # client lookup, any gethostbyaddr) waits 10+ seconds.
            dns_config=kubernetes.client.V1PodDNSConfig(
                local_vars_configuration=MODEL_CONFIGURATION,
                options=[
                    kubernetes.client.V1PodDNSConfigOption(
                        local_vars_configuration=MODEL_CONFIGURATION, name="timeout", value="1"
                    ),
                    kubernetes.client.V1PodDNSConfigOption(
                        local_vars_configuration=MODEL_CONFIGURATION, name="attempts", value="1"
                    ),
                ],
            ),
        ),
    )
