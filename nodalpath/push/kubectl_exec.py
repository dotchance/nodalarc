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

def _deploy_socket_path() -> str:
    val = os.environ.get("NODAL_DEPLOY_SOCKET")
    if val:
        return val
    from nodalarc.platform import get_platform_config
    return get_platform_config().deploy_daemon_unix_socket_path

def _default_namespace() -> str:
    from nodalarc.platform import get_platform_config
    return get_platform_config().kubernetes_namespace

def _default_timeout() -> int:
    from nodalpath.platform import get_nodalpath_config
    return get_nodalpath_config().grpc_push_timeout_seconds

def _max_workers() -> int:
    from nodalpath.platform import get_nodalpath_config
    return get_nodalpath_config().grpc_push_max_parallel_workers


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
    namespace: str | None = None,
    timeout: int | None = None,
) -> ExecResult:
    """Execute vtysh commands on a satellite pod via the deploy daemon.

    Never raises — all errors are captured in ExecResult.
    """
    if namespace is None:
        namespace = _default_namespace()
    if timeout is None:
        timeout = _default_timeout()
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
        sock.connect(_deploy_socket_path())
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
        log.error("Deploy daemon socket not found at %s", _deploy_socket_path())
        return ExecResult(
            node_id=node_id, pod_name=pod_name, success=False,
            stdout="", stderr="Deploy daemon socket not found", returncode=-1,
        )
    except ConnectionRefusedError:
        log.error("Deploy daemon connection refused at %s", _deploy_socket_path())
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
    namespace: str | None = None,
    timeout: int | None = None,
) -> list[ExecResult]:
    """Push vtysh commands to multiple nodes in parallel.

    push_tasks: list of (node_id, commands) tuples.
    Returns results in the same order as input.
    """
    if not push_tasks:
        return []

    if namespace is None:
        namespace = _default_namespace()
    if timeout is None:
        timeout = _default_timeout()

    results: list[ExecResult | None] = [None] * len(push_tasks)

    def _exec_indexed(index: int, node_id: str, commands: str) -> None:
        results[index] = exec_vtysh(node_id, commands, namespace, timeout)

    with ThreadPoolExecutor(max_workers=min(_max_workers(), len(push_tasks))) as pool:
        futures = []
        for i, (node_id, commands) in enumerate(push_tasks):
            futures.append(pool.submit(_exec_indexed, i, node_id, commands))
        for f in futures:
            f.result()  # wait and propagate exceptions

    return results  # type: ignore[return-value]
