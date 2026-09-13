"""Integration test: the browser terminal reaches session pods on any node.

The regression this guards: session pods have the CNI default route replaced
by the routing engine's default (the constellation is the data plane). A pod
can then only answer a peer inside its own node's pod subnet, so the browser
terminal (VS-API to pod SSH) worked for pods co-located with VS-API and
silently timed out for pods on other nodes. The Node Agent installs a
management route to the cluster pod CIDR via the CNI gateway; this test proves
a live session pod on a DIFFERENT node than VS-API is reachable on its SSH
port from the VS-API pod.

The target is chosen through the contracts the Operator publishes on every
session pod: the primary workload container (``nodalarc.io/primary-container``)
and the terminal surface (``nodalarc.io/terminal-access``), read by the same
functions the browser terminal uses. A pod is never selected by a guessed
container name.

Requires a running multi-node session. Skips when the cluster or a current
session is absent, naming why each pod was refused; it never fabricates a pass.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from nodalarc.workload_target import (
    TERMINAL_ACCESS_ANNOTATION,
    WorkloadTargetError,
    workload_target_from_pod,
)
from vs_api.terminal import parse_terminal_contract

pytestmark = pytest.mark.integration

NS = "nodalarc"
_SESSION_POD_SELECTOR = "nodalarc.io/session=true"


@dataclass(frozen=True)
class CrossNodeTarget:
    """One session pod on another node whose published terminal surface is SSH."""

    node_id: str
    pod_name: str
    pod_ip: str
    node: str
    container: str


def eligible_ssh_target(
    pods: Iterable[Mapping[str, Any]], *, vs_api_node: str
) -> tuple[CrossNodeTarget | None, tuple[str, ...]]:
    """The first pod on another node that publishes an SSH terminal, with every refusal named.

    Pods are given in Kubernetes API JSON form. A pod is eligible when it is
    scheduled on a node other than VS-API's, is not being deleted, has a pod
    IP, yields a workload target through the published primary-container
    contract, and declares terminal surface ``ssh``.
    """
    refusals: list[str] = []
    for pod in pods:
        metadata = pod.get("metadata") or {}
        name = str(metadata.get("name") or "<unnamed>")
        node = (pod.get("spec") or {}).get("nodeName")
        if not node:
            refusals.append(f"{name}: not scheduled")
            continue
        if node == vs_api_node:
            refusals.append(f"{name}: on the VS-API node {vs_api_node!r}")
            continue
        if metadata.get("deletionTimestamp"):
            refusals.append(f"{name}: being deleted")
            continue
        pod_ip = (pod.get("status") or {}).get("podIP")
        if not pod_ip:
            refusals.append(f"{name}: no pod IP")
            continue
        try:
            target = workload_target_from_pod(pod)
        except WorkloadTargetError as exc:
            refusals.append(f"{name}: {exc}")
            continue
        annotations = metadata.get("annotations") or {}
        contract = parse_terminal_contract(annotations.get(TERMINAL_ACCESS_ANNOTATION))
        if contract != {"surface": "ssh"}:
            surface = contract.get("surface") if contract else None
            refusals.append(f"{name}: terminal surface {surface!r}, ssh required")
            continue
        chosen = CrossNodeTarget(
            node_id=target.node_id,
            pod_name=target.pod_name,
            pod_ip=str(pod_ip),
            node=str(node),
            container=target.container,
        )
        return chosen, tuple(refusals)
    return None, tuple(refusals)


def _kubectl_json(args: list[str]) -> dict | None:
    result = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _vs_api_pod() -> dict | None:
    data = _kubectl_json(["get", "pods", "-n", NS, "-l", "app=nodalarc-vs-api"])
    items = (data or {}).get("items") or []
    return items[0] if items else None


def _session_pods() -> list[dict]:
    data = _kubectl_json(["get", "pods", "-n", NS, "-l", _SESSION_POD_SELECTOR])
    return (data or {}).get("items") or []


def test_terminal_reaches_a_pod_on_another_node(k3s_available):
    """A session pod NOT co-located with VS-API answers on its SSH port from
    the VS-API pod. This is the exact cross-node path the browser terminal
    takes; before the management route it timed out."""
    vs_pod = _vs_api_pod()
    vs_node = (vs_pod or {}).get("spec", {}).get("nodeName") if vs_pod else None
    if not vs_pod or not vs_node:
        pytest.skip("VS-API is not running")

    pods = _session_pods()
    if not pods:
        pytest.skip("no active session")

    target, refusals = eligible_ssh_target(pods, vs_api_node=vs_node)
    if target is None:
        pytest.skip(
            f"no session pod on a node other than VS-API's ({vs_node}) publishes an ssh "
            f"terminal; {len(refusals)} pods refused: " + "; ".join(refusals)
        )
    print(
        f"cross-node terminal target: node {target.node_id} pod {target.pod_name} "
        f"container {target.container} on {target.node} ({target.pod_ip}) "
        f"probed from VS-API on {vs_node}"
    )

    # From inside the VS-API pod, open TCP to the target pod's SSH port and
    # read the banner. Reachability + a listening sshd is the whole point;
    # a timeout is the pre-fix failure.
    probe = (
        "import socket,sys\n"
        "s=socket.socket();s.settimeout(6)\n"
        f"s.connect(('{target.pod_ip}',22))\n"
        "sys.stdout.write(s.recv(40).decode('latin1'))\n"
    )
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            NS,
            vs_pod["metadata"]["name"],
            "-c",
            "vs-api",
            "--",
            "python",
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"VS-API (node {vs_node}) could not reach {target.pod_name} (node {target.node}, "
        f"{target.pod_ip}:22); cross-node management route missing? stderr={result.stderr}"
    )
    assert "SSH-2.0" in result.stdout, (
        f"expected an SSH banner from {target.pod_name}, got {result.stdout!r}"
    )
