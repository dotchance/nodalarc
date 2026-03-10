"""kubectl exec wrapper for pushing vtysh commands to FRR pods."""

from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger(__name__)

KUBECONFIG: str = "/etc/rancher/k3s/k3s.yaml"
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
    """Execute vtysh commands on a satellite pod via kubectl exec.

    Never raises — all errors are captured in ExecResult.
    """
    pod_name = node_id_to_pod_name(node_id)
    cmd = [
        "kubectl", "exec", "-n", namespace, pod_name,
        "-c", "frr", "--", "vtysh", "-c", commands,
    ]
    env = {**os.environ, "KUBECONFIG": KUBECONFIG}

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
        )
        return ExecResult(
            node_id=node_id,
            pod_name=pod_name,
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        log.error("kubectl exec timed out for %s after %ds", pod_name, timeout)
        return ExecResult(
            node_id=node_id,
            pod_name=pod_name,
            success=False,
            stdout="",
            stderr=f"timeout after {timeout}s",
            returncode=-1,
        )
    except FileNotFoundError:
        log.error("kubectl not found")
        return ExecResult(
            node_id=node_id,
            pod_name=pod_name,
            success=False,
            stdout="",
            stderr="kubectl not found",
            returncode=-1,
        )


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
