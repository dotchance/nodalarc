# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR introspection — execute whitelisted vtysh commands in node containers.

Uses the kubernetes Python client to exec into the node's primary workload
container, which the Operator publishes on the pod.
"""

from __future__ import annotations

import logging

import kubernetes.client
import kubernetes.config
import kubernetes.stream
from nodalarc.platform_config import get_platform_config
from nodalarc.workload_target import (
    WorkloadTargetError,
    read_workload_target,
    validate_node_id,
)

log = logging.getLogger(__name__)

VTYSH_COMMANDS = {
    "show isis neighbor",
    "show ip route",
    "show isis database",
    "show interface brief",
    "show ip ospf neighbor",
    "show ip ospf route",
    "show mpls table",
    "show running-config",
    "show isis interface",
    "show ip route summary",
    "show isis summary",
    "show bgp summary",
}


def run_vtysh(node_id: str, command: str) -> dict:
    """Execute a whitelisted vtysh command in a node's FRR container.

    Uses kubernetes client exec directly — no deploy daemon needed.
    Returns dict with: node_id, command, output, exit_code, error.
    """
    if not node_id:
        raise ValueError("node_id is required")
    if command not in VTYSH_COMMANDS:
        raise ValueError(f"Command not in whitelist: {command}")
    validate_node_id(node_id)

    cfg = get_platform_config()
    namespace = cfg.kubernetes_namespace

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()

    v1 = kubernetes.client.CoreV1Api()

    try:
        target = read_workload_target(v1, namespace, node_id)
    except WorkloadTargetError as exc:
        log.warning("Workload target unavailable for %s cmd=%s: %s", node_id, command, exc)
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": f"workload target unavailable: {exc}",
        }

    try:
        stdout = kubernetes.stream.stream(
            v1.connect_get_namespaced_pod_exec,
            target.pod_name,
            namespace,
            container=target.container,
            command=["vtysh", "-c", command],
            stderr=False,
            stdout=True,
            stdin=False,
            tty=False,
        )
        stderr = ""
        exit_code = 0
    except kubernetes.client.rest.ApiException as exc:
        log.warning(
            "Kubernetes exec failed for %s cmd=%s: %s", node_id, command, exc, exc_info=True
        )
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": "Kubernetes exec failed",
        }
    except Exception as exc:
        log.warning("vtysh exec failed for %s cmd=%s: %s", node_id, command, exc, exc_info=True)
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": "vtysh exec failed",
        }

    if stdout is None:
        log.error("vtysh exec returned None stdout for %s cmd=%s", node_id, command)
        raise ValueError("vtysh exec returned no output")
    max_bytes = cfg.vs_api_introspect_max_response_bytes
    if len(stdout) > max_bytes:
        stdout = stdout[:max_bytes] + "\n... (truncated)"

    error = stderr.strip() if stderr and exit_code != 0 else None

    return {
        "node_id": node_id,
        "command": command,
        "output": stdout,
        "exit_code": exit_code,
        "error": error,
    }
