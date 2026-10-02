"""What the cluster and its servers hold, read without NodalArc.

The platform tests run a make target and then look here. Everything in this module reads:
`kubectl get` against the API server and `ip` over SSH on each server.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KUBECTL = os.environ.get("KUBECTL", "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl")
SERVER_SSH = os.environ.get("SERVER_SSH", "ssh -o BatchMode=yes -o ConnectTimeout=10")
NAMESPACE = os.environ.get("NAMESPACE", "nodalarc")

# Pods come and go on the K3s pod bridge and in K3s's own sandboxes; neither says anything about
# what NodalArc left on a server.
_POD_BRIDGE = "cni0"
_POD_SANDBOX_PREFIX = "cni-"


def _read(command: list[str], timeout: float = 60.0) -> str:
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    assert done.returncode == 0, f"{' '.join(command)} failed: {done.stderr.strip()}"
    return done.stdout


def kubectl(*arguments: str) -> str:
    return _read([*shlex.split(KUBECTL), *arguments]).strip()


def kubectl_items(*arguments: str) -> list[dict[str, Any]]:
    return json.loads(kubectl(*arguments, "-o", "json"))["items"]


def head_commit() -> str:
    return _read(["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=8", "HEAD"]).strip()


def servers() -> dict[str, str]:
    """Every server of the cluster and the address it answers SSH on."""
    return {
        node["metadata"]["name"]: next(
            address["address"]
            for address in node["status"]["addresses"]
            if address["type"] == "InternalIP"
        )
        for node in kubectl_items("get", "nodes")
    }


def nodalarc_objects() -> list[str]:
    """Every namespace, CRD, cluster role and cluster role binding that carries NodalArc's name."""
    names = kubectl(
        "get", "namespace,customresourcedefinition,clusterrole,clusterrolebinding", "-o", "name"
    )
    return [name for name in names.splitlines() if "nodalarc" in name]


def server_network(address: str) -> set[str]:
    """Every link and named network namespace on a server, except what K3s makes per pod.

    The servers run other software with links of its own, so no rule says which links belong to
    NodalArc. A test compares this set at two times.
    """
    links = json.loads(_read([*shlex.split(SERVER_SSH), address, "ip -j -d link"]))
    held = {
        f"link {link['ifname']} ({(link.get('linkinfo') or {}).get('info_kind', 'physical')})"
        for link in links
        if link.get("master") != _POD_BRIDGE
    }
    listed = _read([*shlex.split(SERVER_SSH), address, "ip netns list"])
    held |= {
        f"network namespace {line.split()[0]}"
        for line in listed.splitlines()
        if line.strip() and not line.startswith(_POD_SANDBOX_PREFIX)
    }
    return held


def platform_pods() -> list[dict[str, Any]]:
    """NodalArc's own pods: every pod of the namespace that is not a session pod."""
    return [
        pod
        for pod in kubectl_items("get", "pods", "-n", NAMESPACE)
        if "nodalarc.io/node-id" not in pod["metadata"].get("labels", {})
    ]


def session_pods() -> list[dict[str, Any]]:
    return kubectl_items("get", "pods", "-n", NAMESPACE, "-l", "nodalarc.io/node-id")


def not_ready(pods: list[dict[str, Any]]) -> list[str]:
    """Pods that are not running with every container ready."""
    problems = []
    for pod in pods:
        statuses = pod["status"].get("containerStatuses", [])
        if pod["status"].get("phase") != "Running" or not all(s["ready"] for s in statuses):
            waiting = [f"{s['name']}: {next(iter(s['state']))}" for s in statuses if not s["ready"]]
            problems.append(f"{pod['metadata']['name']} {pod['status'].get('phase')} {waiting}")
    return problems


def nodalarc_images(pods: list[dict[str, Any]]) -> dict[str, str]:
    """Each NodalArc-built image the pods run, keyed by `pod/container`."""
    return {
        f"{pod['metadata']['name']}/{container['name']}": container["image"]
        for pod in pods
        for container in pod["spec"]["containers"]
        if "/nodalarc/" in container["image"]
    }
