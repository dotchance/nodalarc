"""FRR introspection — execute whitelisted vtysh commands in node containers.

Uses the kubernetes Python client to exec into FRR containers directly,
replacing the deploy daemon intermediary.
"""

from __future__ import annotations

import logging
import re

import kubernetes.client
import kubernetes.config
import kubernetes.stream
from nodalarc.platform import get_platform_config

log = logging.getLogger(__name__)

VALID_POD_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")

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

    pod_name = node_id.lower()
    if not VALID_POD_NAME.match(pod_name):
        raise ValueError(f"Invalid pod name: {pod_name}")

    cfg = get_platform_config()
    namespace = cfg.kubernetes_namespace

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()

    v1 = kubernetes.client.CoreV1Api()

    try:
        resp = kubernetes.stream.stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container="frr",
            command=["vtysh", "-c", command],
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
            _preload_content=False,
        )
        stdout = resp.read_stdout(timeout=cfg.vs_api_introspect_command_timeout_seconds)
        stderr = resp.read_stderr(timeout=1)
        exit_code = resp.returncode if hasattr(resp, "returncode") else 0
    except kubernetes.client.rest.ApiException as e:
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": f"K8s exec failed: {e.reason}",
        }
    except Exception as exc:
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": str(exc),
        }

    output = stdout or ""
    max_bytes = cfg.vs_api_introspect_max_response_bytes
    if len(output) > max_bytes:
        output = output[:max_bytes] + "\n... (truncated)"

    error = stderr.strip() if stderr and exit_code != 0 else None

    return {
        "node_id": node_id,
        "command": command,
        "output": output,
        "exit_code": exit_code or 0,
        "error": error,
    }
