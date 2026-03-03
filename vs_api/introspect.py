"""Networking introspection — run whitelisted vtysh commands inside FRR containers."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)

_KUBECONFIG = os.environ.get("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")

VTYSH_COMMANDS = {
    "show isis neighbor",
    "show ip route",
    "show isis database",
    "show interface brief",
    "show ip ospf neighbor",
    "show ip ospf route",
    "show mpls table",
    "show running-config",
}

_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB
_NAMESPACE = "nodalarc"
_TIMEOUT_S = 15


def run_vtysh(node_id: str, command: str) -> dict:
    """Execute a whitelisted vtysh command in a node's FRR container.

    Returns dict with: node_id, command, output, exit_code, error.
    Raises ValueError for invalid commands or missing node_id.
    """
    if not node_id:
        raise ValueError("node_id is required")
    if command not in VTYSH_COMMANDS:
        raise ValueError(f"Command not in whitelist: {command}")

    pod_name = node_id.lower()

    env = {**os.environ, "KUBECONFIG": _KUBECONFIG}

    try:
        result = subprocess.run(
            [
                "kubectl", "exec", "-n", _NAMESPACE, pod_name,
                "-c", "frr", "--",
                "vtysh", "-c", command,
            ],
            capture_output=True, text=True, timeout=_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning(f"vtysh timeout for {node_id}: {command}")
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": "Command timed out",
        }

    output = result.stdout
    if len(output) > _MAX_OUTPUT_BYTES:
        output = output[:_MAX_OUTPUT_BYTES] + "\n... (truncated at 64KB)"

    error = result.stderr.strip() if result.returncode != 0 else None

    return {
        "node_id": node_id,
        "command": command,
        "output": output,
        "exit_code": result.returncode,
        "error": error,
    }
