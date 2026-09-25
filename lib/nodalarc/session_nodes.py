# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Kubernetes nodes that run session pods.

A node runs session pods when it carries the Node Agent label and no
``NoSchedule`` not-ready taint. The Operator places pods on these nodes and
VS-API sizes its capacity warning from them; both read this one definition.
"""

from __future__ import annotations

from typing import Any

NODE_AGENT_LABEL_SELECTOR = "nodalarc.io/node-agent=true"
NOT_READY_TAINT_KEY = "nodalarc.io/not-ready"


def available_session_nodes(core_v1: Any) -> list[str]:
    """Names of the nodes that accept session pods, sorted.

    A failed node listing raises: there is no node count to report without it.
    """
    nodes = core_v1.list_node(label_selector=NODE_AGENT_LABEL_SELECTOR)
    return sorted(
        node.metadata.name
        for node in nodes.items
        if not any(
            taint.key == NOT_READY_TAINT_KEY and taint.effect == "NoSchedule"
            for taint in node.spec.taints or []
        )
    )
