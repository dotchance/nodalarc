# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Kubernetes nodes that run session pods.

A node runs session pods when its Node Agent is ready: the Node Agent runs
there and the host passes node qualification (``node_agent.qualification``).
The Operator places pods on these nodes and VS-API sizes its capacity
warning from them; both read this one definition.
"""

from __future__ import annotations

from typing import Any

NODE_AGENT_POD_SELECTOR = "app=nodalarc-node-agent"


def _ready(pod: Any) -> bool:
    return any(
        condition.type == "Ready" and condition.status == "True"
        for condition in (pod.status.conditions if pod.status else None) or ()
    )


def available_session_nodes(core_v1: Any, namespace: str) -> list[str]:
    """Names of the nodes whose Node Agent is ready, sorted.

    A failed pod listing raises: there is no node count to report without it.
    """
    pods = core_v1.list_namespaced_pod(namespace, label_selector=NODE_AGENT_POD_SELECTOR)
    return sorted({pod.spec.node_name for pod in pods.items if pod.spec.node_name and _ready(pod)})
