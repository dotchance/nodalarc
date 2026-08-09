"""Integration test: the browser terminal reaches session pods on any node.

The regression this guards: session pods have the CNI default route replaced
by the routing engine's default (the constellation is the data plane). A pod
can then only answer a peer inside its own node's pod subnet — so the browser
terminal (VS-API -> pod SSH/exec) worked for pods co-located with VS-API and
silently timed out for pods on other nodes. The Node Agent now installs a
management route to the cluster pod CIDR via the CNI gateway; this test proves
a live session pod on a DIFFERENT node than VS-API is reachable on its SSH
port from the VS-API pod.

Requires a running multi-node session. Skips cleanly when the cluster or a
current session is absent — it never fabricates a pass.
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.integration

NS = "nodalarc"


def _kubectl_json(args: list[str]) -> dict | None:
    result = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _vs_api_node() -> str | None:
    data = _kubectl_json(["get", "pods", "-n", NS, "-l", "app=nodalarc-vs-api"])
    items = (data or {}).get("items") or []
    if not items:
        return None
    return items[0]["spec"].get("nodeName")


def _session_pods() -> list[dict]:
    data = _kubectl_json(["get", "pods", "-n", NS, "-l", "nodalarc.io/session=true"])
    return (data or {}).get("items") or []


def test_terminal_reaches_a_pod_on_another_node(k3s_available):
    """A session pod NOT co-located with VS-API answers on its SSH port from
    the VS-API pod. This is the exact cross-node path the browser terminal
    takes; before the management route it timed out."""
    vs_node = _vs_api_node()
    if not vs_node:
        pytest.skip("VS-API is not running")

    pods = _session_pods()
    if not pods:
        pytest.skip("no active session")

    # An FRR router pod on a node other than VS-API's, with a pod IP.
    target = None
    for pod in pods:
        node = pod["spec"].get("nodeName")
        pod_ip = (pod.get("status") or {}).get("podIP")
        names = [c["name"] for c in pod["spec"]["containers"]]
        if node and node != vs_node and pod_ip and "frr" in names:
            target = (pod["metadata"]["name"], pod_ip, node)
            break
    if target is None:
        pytest.skip(f"no FRR session pod on a node other than VS-API's (vs-api on {vs_node})")

    name, pod_ip, node = target
    vs_pod = _kubectl_json(["get", "pods", "-n", NS, "-l", "app=nodalarc-vs-api"])["items"][0][
        "metadata"
    ]["name"]

    # From inside the VS-API pod, open TCP to the target pod's SSH port and
    # read the banner. Reachability + a listening sshd is the whole point;
    # a timeout is the pre-fix failure.
    probe = (
        "import socket,sys\n"
        f"s=socket.socket();s.settimeout(6)\n"
        f"s.connect(('{pod_ip}',22))\n"
        "sys.stdout.write(s.recv(40).decode('latin1'))\n"
    )
    result = subprocess.run(
        ["kubectl", "exec", "-n", NS, vs_pod, "-c", "vs-api", "--", "python", "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"VS-API (node {vs_node}) could not reach {name} (node {node}, "
        f"{pod_ip}:22) — cross-node management route missing? stderr={result.stderr}"
    )
    assert "SSH-2.0" in result.stdout, f"expected an SSH banner from {name}, got {result.stdout!r}"
