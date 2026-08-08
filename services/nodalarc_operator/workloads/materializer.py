# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The one shared Pod assembly for session workloads.

Every session pod — the built-in FRR composition today, explicit workload
profiles behind it — is assembled here and only here. The materializer owns
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

SESSION_LABEL = "nodalarc.io/session"
NODE_ID_LABEL = "nodalarc.io/node-id"
ROLE_LABEL = "nodalarc.io/role"

# Platform-owned pod annotation carrying the built-in-or-explicit workload
# selection identity. The reconciler compares it against the CR's current
# selection; a differing pod is deleted and recreated, never re-stamped.
WORKLOAD_SELECTION_ANNOTATION = "nodalarc.io/workload-selection"

WIRING_STATUS_CONFIGMAP = "nodalarc-wiring-status"

_WIRING_GATE_SCRIPT = (
    'my_netns="$(readlink /proc/self/ns/net)"\n'
    'my_netns="${my_netns#net:[}"\n'
    'my_netns="${my_netns%]}"\n'
    'status_file="/wiring-status/status.json"\n'
    'echo "waiting for platform wiring of ${NODE_ID} '
    '(pod ${POD_UID}, run ${SESSION_RUN_ID}, netns ${my_netns})"\n'
    "while true; do\n"
    '  if [ -f "${status_file}" ] && jq -e --arg uid "${POD_UID}" '
    '--arg run "${SESSION_RUN_ID}" --arg ns "${my_netns}" '
    '\'.status == "ready" and .dirty_kernel == false '
    "and .pod_uid == $uid and .session_run_id == $run "
    "and .netns_id == $ns "
    'and (.phases | length > 0) and (.phases | all(.status == "ready"))\' '
    '"${status_file}" > /dev/null 2>&1; then\n'
    '    echo "wiring ready for ${NODE_ID}"\n'
    "    exit 0\n"
    "  fi\n"
    "  sleep 2\n"
    "done\n"
)


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
    init_containers: list[kubernetes.client.V1Container] = field(default_factory=list)


def _wiring_gate_container() -> kubernetes.client.V1Container:
    # Platform wiring gate: authored containers start only after the Node
    # Agent has wired THIS pod incarnation. The gate observes the existing
    # wiring proof (only this node's key of the nodalarc-wiring-status
    # ConfigMap, projected as an optional volume) and exits when the proof
    # reports ready with a clean kernel AND names this exact incarnation
    # and run: pod UID (downward API), session run label, and the inode of
    # the network namespace the gate itself runs in. A row written for a
    # replaced pod, a recreated sandbox, or a previous run can never
    # release the workload. The gate never times out: wiring that does not
    # complete must surface as a pod stuck in Init, not as a workload
    # started on an unwired network.
    return kubernetes.client.V1Container(
        name="wiring-gate",
        image=_require_env("WIRING_GATE_IMAGE"),
        image_pull_policy=_require_env("IMAGE_PULL_POLICY"),
        command=["bash", "-c", _WIRING_GATE_SCRIPT],
        env=[
            kubernetes.client.V1EnvVar(
                name="NODE_ID",
                value_from=kubernetes.client.V1EnvVarSource(
                    field_ref=kubernetes.client.V1ObjectFieldSelector(
                        field_path=f"metadata.labels['{NODE_ID_LABEL}']"
                    )
                ),
            ),
            kubernetes.client.V1EnvVar(
                name="POD_UID",
                value_from=kubernetes.client.V1EnvVarSource(
                    field_ref=kubernetes.client.V1ObjectFieldSelector(field_path="metadata.uid")
                ),
            ),
            kubernetes.client.V1EnvVar(
                name="SESSION_RUN_ID",
                value_from=kubernetes.client.V1EnvVarSource(
                    field_ref=kubernetes.client.V1ObjectFieldSelector(
                        field_path=f"metadata.labels['{POD_SESSION_RUN_LABEL}']"
                    )
                ),
            ),
        ],
        security_context=kubernetes.client.V1SecurityContext(
            capabilities=kubernetes.client.V1Capabilities(drop=["ALL"]),
            read_only_root_filesystem=True,
            allow_privilege_escalation=False,
        ),
        resources=kubernetes.client.V1ResourceRequirements(
            requests={"memory": "16Mi", "cpu": "10m"},
            limits={"memory": "32Mi", "cpu": "100m"},
        ),
        volume_mounts=[
            kubernetes.client.V1VolumeMount(
                name="wiring-status", mount_path="/wiring-status", read_only=True
            ),
        ],
    )


def _wiring_status_volume(node_id: str) -> kubernetes.client.V1Volume:
    return kubernetes.client.V1Volume(
        name="wiring-status",
        config_map=kubernetes.client.V1ConfigMapVolumeSource(
            name=WIRING_STATUS_CONFIGMAP,
            # Project only this node's proof, never the whole multi-node
            # status document. The proof appears only after the Node Agent
            # wires; the pod must be creatable before it exists.
            items=[
                kubernetes.client.V1KeyToPath(key=node_id, path="status.json"),
            ],
            optional=True,
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
    if any(volume.name == "wiring-status" for volume in composition.volumes):
        raise ValueError("composition may not use the reserved volume name 'wiring-status'")

    return kubernetes.client.V1Pod(
        metadata=kubernetes.client.V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels=labels,
            annotations={WORKLOAD_SELECTION_ANNOTATION: selection_identity},
            owner_references=[owner_ref],
        ),
        spec=kubernetes.client.V1PodSpec(
            node_name=target_node,
            init_containers=[_wiring_gate_container(), *composition.init_containers],
            containers=list(composition.containers),
            volumes=[*composition.volumes, _wiring_status_volume(node_id)],
            restart_policy="Never",
            automount_service_account_token=False,
            # Fast DNS timeout: pod IPs have no PTR records in CoreDNS.
            # Without this, every reverse DNS lookup (traceroute hops, sshd
            # client lookup, any gethostbyaddr) waits 10+ seconds.
            dns_config=kubernetes.client.V1PodDNSConfig(
                options=[
                    kubernetes.client.V1PodDNSConfigOption(name="timeout", value="1"),
                    kubernetes.client.V1PodDNSConfigOption(name="attempts", value="1"),
                ],
            ),
        ),
    )
