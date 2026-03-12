"""Networking introspection — run whitelisted vtysh commands inside FRR containers.

Routes kubectl exec through the deploy daemon which holds the privileged
KUBECONFIG. The VS-API never reads /etc/rancher/k3s/k3s.yaml directly.
"""

from __future__ import annotations

import json
import logging
import re
import socket

log = logging.getLogger(__name__)

# Defense-in-depth: validate pod names even though deploy daemon also validates
VALID_POD_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")

from nodalarc.platform import get_platform_config

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

def _daemon_request(req: dict, timeout: float | None = None) -> dict:
    """Send a request to the deploy daemon and receive the response."""
    cfg = get_platform_config()
    if timeout is None:
        timeout = cfg.vs_api_introspect_command_timeout_seconds
    deploy_socket = cfg.deploy_daemon_unix_socket_path
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(deploy_socket)
        data = json.dumps(req) + "\n"
        sock.sendall(data.encode())
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line = buf[:buf.index(b"\n")]
                return json.loads(line)
        return {"ok": False, "error": "No response from deploy daemon"}
    except FileNotFoundError:
        return {"ok": False, "error": "Deploy daemon not running"}
    except ConnectionRefusedError:
        return {"ok": False, "error": "Deploy daemon connection refused"}
    except socket.timeout:
        return {"ok": False, "error": "Command timed out"}
    finally:
        sock.close()


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
    if not VALID_POD_NAME.match(pod_name):
        raise ValueError(f"Invalid pod name: {pod_name}")

    resp = _daemon_request({
        "action": "kubectl_exec",
        "pod": pod_name,
        "container": "frr",
        "command": ["vtysh", "-c", command],
    })

    if "error" in resp and not resp.get("ok"):
        return {
            "node_id": node_id,
            "command": command,
            "output": "",
            "exit_code": -1,
            "error": resp["error"],
        }

    output = resp.get("stdout", "")
    max_bytes = get_platform_config().vs_api_introspect_max_response_bytes
    if len(output) > max_bytes:
        output = output[:max_bytes] + "\n... (truncated)"

    exit_code = resp.get("exit_code", -1)
    error = resp.get("stderr", "").strip() if exit_code != 0 else None

    return {
        "node_id": node_id,
        "command": command,
        "output": output,
        "exit_code": exit_code,
        "error": error,
    }
