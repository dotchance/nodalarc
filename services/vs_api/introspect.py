# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""FRR introspection — execute whitelisted vtysh commands in node containers.

Uses the kubernetes Python client to exec into the node's primary workload
container, which the Operator publishes on the pod.
"""

from __future__ import annotations

import logging

import kubernetes.client
import kubernetes.stream
from nodalarc.models.vs_api import IntrospectResult
from nodalarc.platform_config import get_platform_config
from nodalarc.workload_target import (
    read_workload_target,
    validate_node_id,
)

from vs_api import k8s

log = logging.getLogger(__name__)

# How long a command may run before it counts as an exec that failed.
_EXEC_DEADLINE_S = 60

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


class IntrospectExecError(Exception):
    """The command could not be executed inside the node's workload container.

    The message is fixed per failure kind; the Kubernetes client's text stays on
    the chained cause for the server log.
    """


def run_vtysh(node_id: str, command: str) -> IntrospectResult:
    """Execute a whitelisted vtysh command in a node's FRR container.

    Raises ``ValueError`` for an invalid request, ``WorkloadTargetError`` when
    the node has no published workload target, and ``IntrospectExecError`` when
    the exec itself fails.
    """
    if not node_id:
        raise ValueError("node_id is required")
    if command not in VTYSH_COMMANDS:
        raise ValueError(f"Command not in whitelist: {command}")
    validate_node_id(node_id)

    cfg = get_platform_config()
    namespace = cfg.kubernetes_namespace

    v1 = k8s.core_v1()

    target = read_workload_target(v1, namespace, node_id)

    try:
        resp = kubernetes.stream.stream(
            v1.connect_get_namespaced_pod_exec,
            target.pod_name,
            namespace,
            container=target.container,
            command=["vtysh", "-c", command],
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
            _preload_content=False,
        )
    except kubernetes.client.rest.ApiException as exc:
        log.warning(
            "Kubernetes exec failed for %s cmd=%s: %s", node_id, command, exc, exc_info=True
        )
        raise IntrospectExecError("Kubernetes exec failed") from exc
    except Exception as exc:
        log.warning("vtysh exec failed for %s cmd=%s: %s", node_id, command, exc, exc_info=True)
        raise IntrospectExecError("vtysh exec failed") from exc

    try:
        try:
            resp.run_forever(timeout=_EXEC_DEADLINE_S)
            finished = not resp.is_open()
            if finished:
                # The status channel carries the exit status once the command ends; it
                # does not parse when vtysh never started.
                exit_code = resp.returncode
                stdout = resp.read_stdout()
                stderr = resp.read_stderr()
        except Exception as exc:
            log.warning("vtysh exec failed for %s cmd=%s: %s", node_id, command, exc, exc_info=True)
            raise IntrospectExecError("vtysh exec failed") from exc
    finally:
        resp.close()
    if not finished:
        log.warning("vtysh did not finish for %s cmd=%s", node_id, command)
        raise IntrospectExecError(f"vtysh did not finish within {_EXEC_DEADLINE_S} s")

    max_bytes = cfg.vs_api_introspect_max_response_bytes
    if len(stdout) > max_bytes:
        stdout = stdout[:max_bytes] + "\n... (truncated)"
    if len(stderr) > max_bytes:
        stderr = stderr[:max_bytes] + "\n... (truncated)"

    return IntrospectResult(
        node_id=node_id, command=command, output=stdout, exit_code=exit_code, stderr=stderr
    )
