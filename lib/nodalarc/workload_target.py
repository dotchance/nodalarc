"""The primary workload target of one session node: its pod and container.

The Operator composes every session pod and is the only authority for which
container runs a node's primary workload. It publishes that name on the pod
it creates, and every consumer that reaches into a node (introspection,
tracing, measurement, qualification) reads it here. The target is independent
of terminal access: SSH reachability, container identity and CLI command
support are separate facts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

NODE_ID_LABEL = "nodalarc.io/node-id"
PRIMARY_CONTAINER_ANNOTATION = "nodalarc.io/primary-container"

_NODE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")


class WorkloadTargetError(Exception):
    """The node's primary workload target cannot be established."""

    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(f"{node_id}: {message}")
        self.node_id = node_id


class WorkloadTarget(BaseModel):
    """One node's live pod and the container running its primary workload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    namespace: str
    pod_name: str
    pod_uid: str
    container: str


def validate_node_id(node_id: object) -> str:
    """Accept only runtime node identifiers, which are also safe label values."""
    if not isinstance(node_id, str) or not _NODE_ID_PATTERN.match(node_id):
        raise ValueError(f"invalid node id: {node_id!r}")
    return node_id


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def workload_target_from_pod(pod: Mapping[str, Any]) -> WorkloadTarget:
    """Read the target published on one pod, given in Kubernetes API JSON form."""
    metadata = _mapping(pod.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    annotations = _mapping(metadata.get("annotations"))
    pod_name = str(metadata.get("name") or "")
    node_id = labels.get(NODE_ID_LABEL)
    if not isinstance(node_id, str) or not node_id:
        raise WorkloadTargetError(
            pod_name or "<unnamed pod>", f"pod {pod_name!r} carries no {NODE_ID_LABEL} label"
        )
    container = annotations.get(PRIMARY_CONTAINER_ANNOTATION)
    if not isinstance(container, str) or not container:
        raise WorkloadTargetError(
            node_id, f"pod {pod_name!r} carries no {PRIMARY_CONTAINER_ANNOTATION} annotation"
        )
    declared = [
        str(entry.get("name"))
        for entry in _mapping(pod.get("spec")).get("containers") or []
        if isinstance(entry, Mapping)
    ]
    if container not in declared:
        raise WorkloadTargetError(
            node_id,
            f"pod {pod_name!r} annotates primary container {container!r} but declares {declared}",
        )
    namespace = metadata.get("namespace")
    pod_uid = metadata.get("uid")
    if not pod_name or not isinstance(namespace, str) or not namespace:
        raise WorkloadTargetError(node_id, f"pod {pod_name!r} lacks a name or namespace")
    if not isinstance(pod_uid, str) or not pod_uid:
        raise WorkloadTargetError(node_id, f"pod {pod_name!r} lacks a uid")
    return WorkloadTarget(
        node_id=node_id,
        namespace=namespace,
        pod_name=pod_name,
        pod_uid=pod_uid,
        container=container,
    )


def select_live_pod(pods: Iterable[Mapping[str, Any]], node_id: str) -> Mapping[str, Any]:
    """The one pod of a node that is not being deleted; zero or several is a refusal."""
    live = [pod for pod in pods if not _mapping(pod.get("metadata")).get("deletionTimestamp")]
    if len(live) != 1:
        names = sorted(str(_mapping(pod.get("metadata")).get("name")) for pod in live)
        raise WorkloadTargetError(
            node_id, f"expected one live session pod, found {len(live)}: {names}"
        )
    return live[0]


def read_workload_target(core_v1: Any, namespace: str, node_id: str) -> WorkloadTarget:
    """Read the node's current target through a CoreV1Api client.

    Every call reads the live pod set, so a replaced pod is never targeted
    through a stale identity. Pod objects from the client are converted to
    their API JSON form; mappings are accepted as they are.
    """
    import kubernetes.client

    validate_node_id(node_id)
    try:
        listed = core_v1.list_namespaced_pod(namespace, label_selector=f"{NODE_ID_LABEL}={node_id}")
    except kubernetes.client.rest.ApiException as exc:
        raise WorkloadTargetError(
            node_id, f"pod listing failed: HTTP {exc.status} {exc.reason}"
        ) from exc
    serializer = kubernetes.client.ApiClient.sanitize_for_serialization
    pods = [
        item if isinstance(item, Mapping) else serializer(core_v1.api_client, item)
        for item in listed.items
    ]
    target = workload_target_from_pod(select_live_pod(pods, node_id))
    if target.node_id != node_id:
        raise WorkloadTargetError(
            node_id, f"pod {target.pod_name!r} is labelled for node {target.node_id!r}"
        )
    return target
