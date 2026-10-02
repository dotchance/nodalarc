"""Faults, made the way the world makes them: outside NodalArc's API.

A resilience test breaks one thing on purpose and from then on acts only as the operator. This
module is the only place the tests reach around VS-API. It breaks things. It never reads state
for an assertion and it never repairs.
"""

from __future__ import annotations

import os
import shlex
import subprocess

from nodalarc.runtime_naming import gs_bridge_port_name
from nodalarc.workload_target import NODE_ID_LABEL

KUBECTL = os.environ.get("KUBECTL", "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl")
NAMESPACE = os.environ.get("NAMESPACE", "nodalarc")


def _kubectl(*arguments: str) -> str:
    done = subprocess.run(
        [*shlex.split(KUBECTL), *arguments], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, f"kubectl {' '.join(arguments)} failed: {done.stderr.strip()}"
    return done.stdout.strip()


def cut_ground_terminal(ground: str, interface: str) -> str:
    """Take down the server-side interface behind one ground terminal of the running session.

    NodalArc did not command it, so the kernel no longer matches what the Scheduler holds as
    actual. Returns the interface that was taken down.
    """
    assert interface.startswith("term") and interface[4:].isdigit(), interface
    host_interface = gs_bridge_port_name(ground, int(interface[4:]))
    server = _kubectl(
        "get", "pods", "-n", NAMESPACE, "-l", f"{NODE_ID_LABEL}={ground}",
        "-o", "jsonpath={.items[0].spec.nodeName}",
    )  # fmt: skip
    node_agent = _kubectl(
        "get", "pods", "-n", NAMESPACE, "-l", "app=nodalarc-node-agent",
        "--field-selector", f"spec.nodeName={server}", "-o", "jsonpath={.items[0].metadata.name}",
    )  # fmt: skip
    # The Node Agent image carries no `ip`; its own netlink library takes the interface down.
    _kubectl(
        "exec", "-n", NAMESPACE, node_agent, "-c", "node-agent", "--", "python3", "-c",
        "import sys; from pyroute2 import IPRoute; ipr = IPRoute(); "
        "ipr.link('set', index=ipr.link_lookup(ifname=sys.argv[1])[0], state='down')",
        host_interface,
    )  # fmt: skip
    return host_interface
