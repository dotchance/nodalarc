# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""PID discovery for pods on the local K3s node.

Migrated from orchestrator/link_manager.py:71-111. The DaemonSet
variant filters by spec.nodeName so each agent only discovers pods
on its own node.

IMPORTANT — node ID case sensitivity:
  The node_id returned here comes from the K8s label "nodalarc.io/node-id",
  which uses the canonical case from the AddressingScheme (e.g., "sat-P01S02").
  All gRPC messages must use this canonical case because ground bridge naming
  helpers derive host veth names from the node ID, and Linux interface names
  are case-sensitive. K8s pod names are lowercase; node IDs are not.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def discover_local_pod_pids(
    namespace: str | None = None,
    node_name: str | None = None,
    label_selector: str = "nodalarc.io/role",
) -> dict[str, int]:
    """Discover container PIDs for pods on this K3s node.

    Uses K8s API -> container ID -> crictl inspect -> PID.
    Returns {node_id: pid}.

    Args:
        namespace: K8s namespace (defaults to platform config).
        node_name: K3s node name to filter by. Defaults to NODE_NAME env var.
        label_selector: Label selector for Nodal Arc pods.
    """
    import kubernetes
    import kubernetes.client
    import kubernetes.config

    if namespace is None:
        from nodalarc.platform_config import get_platform_config

        namespace = get_platform_config().kubernetes_namespace

    if node_name is None:
        node_name = os.environ.get("NODE_NAME", "")

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.config_exception.ConfigException:
        kubernetes.config.load_kube_config()

    v1 = kubernetes.client.CoreV1Api()

    # Filter to pods on this node only (DaemonSet pattern)
    field_selector = f"spec.nodeName={node_name}" if node_name else ""
    pods = v1.list_namespaced_pod(
        namespace,
        label_selector=label_selector,
        field_selector=field_selector,
    )

    result: dict[str, int] = {}
    for pod in pods.items:
        node_id = pod.metadata.labels.get("nodalarc.io/node-id")
        if not node_id:
            continue
        if not pod.status or not pod.status.container_statuses:
            continue
        container_id = pod.status.container_statuses[0].container_id
        if not container_id:
            continue
        # Strip containerd:// prefix
        raw_id = container_id.split("://", 1)[-1]
        # crictl inspect -> parse JSON -> info.pid
        try:
            # Use CONTAINER_RUNTIME_ENDPOINT env var if set (K3s uses a non-standard path)
            crictl_cmd = ["crictl"]
            runtime_ep = os.environ.get("CONTAINER_RUNTIME_ENDPOINT")
            if runtime_ep:
                crictl_cmd.extend(["--runtime-endpoint", runtime_ep])
            proc = subprocess.run(
                [*crictl_cmd, "inspect", raw_id],
                capture_output=True,
                text=True,
                check=True,
            )
            info = json.loads(proc.stdout)
            pid = info["info"]["pid"]
            result[node_id] = pid
            log.info("Discovered %s -> PID %d", node_id, pid)
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
            log.warning("Failed to discover PID for %s: %s", node_id, exc)

    log.info("Discovered PIDs for %d pods on node %s", len(result), node_name or "(all)")
    return result
