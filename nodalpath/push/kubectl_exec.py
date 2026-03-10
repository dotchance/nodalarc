"""Deploy daemon client for pushing vtysh commands to FRR pods.

Routes all kubectl exec operations through the deploy daemon Unix socket.
NodalPath never holds the kubeconfig or calls kubectl directly.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEPLOY_SOCKET_PATH: str = os.environ.get("NODAL_DEPLOY_SOCKET", "/tmp/nodal-deploy.sock")
DEFAULT_NAMESPACE: str = "nodalarc"
DEFAULT_TIMEOUT: int = 10
MAX_WORKERS: int = 20


@dataclass
class ExecResult:
    """Result of a kubectl exec invocation."""
    node_id: str
    pod_name: str
    success: bool
    stdout: str
    stderr: str
    returncode: int


def node_id_to_pod_name(node_id: str) -> str:
    """Convert a node_id to the corresponding K3s pod name."""
    return node_id.lower()


def exec_vtysh(
    node_id: str,
    commands: str,
    namespace: str = DEFAULT_NAMESPACE,
    timeout: int = DEFAULT_TIMEOUT,
) -> ExecResult:
    """Execute vtysh commands on a satellite pod via the deploy daemon.

    Never raises — all errors are captured in ExecResult.
    """
    pod_name = node_id_to_pod_name(node_id)
    req = {
        "action": "kubectl_exec",
        "pod": pod_name,
        "container": "frr",
        "command": ["vtysh", "-c", commands],
    }
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(DEPLOY_SOCKET_PATH)
        sock.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line = buf[:buf.index(b"\n")]
                resp = json.loads(line)
                success = resp.get("ok", False)
                return ExecResult(
                    node_id=node_id,
                    pod_name=pod_name,
                    success=success,
                    stdout=resp.get("stdout", ""),
                    stderr=resp.get("stderr", resp.get("error", "")),
                    returncode=resp.get("exit_code", 0 if success else 1),
                )
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr="No response from deploy daemon", returncode=-1,
        )
    except FileNotFoundError:
        log.error("Deploy daemon socket not found at %s", DEPLOY_SOCKET_PATH)
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr="Deploy daemon socket not found", returncode=-1,
        )
    except ConnectionRefusedError:
        log.error("Deploy daemon connection refused at %s", DEPLOY_SOCKET_PATH)
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr="Deploy daemon connection refused", returncode=-1,
        )
    except socket.timeout:
        log.error("Deploy daemon timed out for %s after %ds", pod_name, timeout)
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr=f"Timeout after {timeout}s", returncode=-1,
        )
    except Exception as exc:
        log.error("Deploy daemon error for %s: %s", pod_name, exc)
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr=str(exc), returncode=-1,
        )
    finally:
        sock.close()


def push_to_nodes(
    push_tasks: list[tuple[str, str]],
    namespace: str = DEFAULT_NAMESPACE,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[ExecResult]:
    """Push vtysh commands to multiple nodes in parallel.

    push_tasks: list of (node_id, commands) tuples.
    Returns results in the same order as input.
    """
    if not push_tasks:
        return []

    results: list[ExecResult | None] = [None] * len(push_tasks)

    def _exec_indexed(index: int, node_id: str, commands: str) -> None:
        results[index] = exec_vtysh(node_id, commands, namespace, timeout)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(push_tasks))) as pool:
        futures = []
        for i, (node_id, commands) in enumerate(push_tasks):
            futures.append(pool.submit(_exec_indexed, i, node_id, commands))
        for f in futures:
            f.result()  # wait and propagate exceptions

    return results  # type: ignore[return-value]
