# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Pod location map — maps canonical node IDs to K3s nodes.

IMPORTANT — node identity contract:
  Runtime node IDs come from the resolved session. K8s pod names are sanitized
  deployment names and are NEVER used as node IDs. The K8s label
  "nodalarc.io/node-id" carries the canonical runtime node ID set by the
  Operator from resolved session truth.

  This module reads the label value directly — it does not transform or
  derive node IDs from pod names. The canonical ID flows unchanged from
  the label into NATS request/reply message fields.

The Scheduler uses this to:
  1. Route BatchLinkDown/Up to the correct Node Agent (by K3s node)
  2. Build interface_map keys using canonical node IDs
"""

from __future__ import annotations

import logging

from nodalarc.substrate.manifest_contract import POD_SESSION_RUN_LABEL
from nodalarc.workload_target import NODE_ID_LABEL

log = logging.getLogger(__name__)


class PodLocationMap:
    """Maps canonical node IDs to K3s node locations, read from the K8s API."""

    def __init__(self) -> None:
        # canonical_node_id -> k3s node name
        self._node_of: dict[str, str] = {}
        self._node_ips: dict[str, str] = {}  # k3s_node_name -> InternalIP
        # k3s node name -> Node Agent NATS subject
        self._agent_addrs: dict[str, str] = {}

    @property
    def node_ids(self) -> list[str]:
        """All canonical node IDs."""
        return list(self._node_of.keys())

    def k3s_node(self, node_id: str) -> str:
        """Get K3s node name for a canonical node ID."""
        return self._node_of.get(node_id, "")

    def agent_addr(self, node_id: str) -> str:
        """Get Node Agent NATS subject for the K3s node hosting this pod."""
        k3s = self._node_of.get(node_id, "")
        return self._agent_addrs.get(k3s, "")

    def node_ip(self, k3s_node: str) -> str:
        """Get the InternalIP for a K3s node. Empty string if unknown."""
        return self._node_ips.get(k3s_node, "")

    def link_locality(self, node_a: str, node_b: str) -> int | None:
        """Determine link locality. Returns None if either pod is unscheduled."""
        from nodalarc.proto import node_agent_pb2

        k3s_a = self._node_of.get(node_a, "")
        k3s_b = self._node_of.get(node_b, "")
        if not k3s_a or not k3s_b:
            return None
        if k3s_a != k3s_b:
            return node_agent_pb2.LOCALITY_CROSS_NODE
        return node_agent_pb2.LOCALITY_LOCAL

    def all_agent_addrs(self) -> list[str]:
        """All unique Node Agent NATS subjects."""
        return list(set(self._agent_addrs.values()))

    def nodes_on_agent(self, agent_addr: str) -> list[str]:
        """All node IDs hosted by a given agent."""
        target_k3s = None
        for k3s, addr in self._agent_addrs.items():
            if addr == agent_addr:
                target_k3s = k3s
                break
        if target_k3s is None:
            return []
        return [nid for nid, k3s in self._node_of.items() if k3s == target_k3s]

    def load_from_k8s_api(
        self,
        *,
        namespace: str,
        expected_node_ids: set[str] | frozenset[str],
        session_id: str,
    ) -> None:
        """Load the active session's pod locations from the K8s API.

        Reads canonical node IDs from the nodalarc.io/node-id label and the
        K3s node from pod.spec.nodeName. Node Agent NATS subjects are the K3s
        node name (e.g. nodalarc.agent.{node_name}).

        Only pods of the resolved active session are located: namespace-wide
        discovery would let stale pods from a previous session enter the
        dispatch and wiring authority. The Scheduler runs only in-cluster, so
        the in-cluster configuration is the only one it loads.
        """
        import kubernetes.client
        import kubernetes.config

        expected = set(expected_node_ids)
        if not expected:
            raise ValueError("expected_node_ids must not be empty")

        kubernetes.config.load_incluster_config()
        v1 = kubernetes.client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace, label_selector=NODE_ID_LABEL)

        for pod in pods.items:
            # Canonical node ID from label — NOT from pod.metadata.name
            labels = dict(getattr(pod.metadata, "labels", None) or {})
            node_id = labels.get(NODE_ID_LABEL)
            if not node_id:
                continue
            if node_id not in expected:
                continue
            if labels.get(POD_SESSION_RUN_LABEL) != session_id:
                continue
            if node_id in self._node_of:
                raise RuntimeError(f"Duplicate active session pod location for node {node_id}")

            k3s_node = pod.spec.node_name or ""
            if not k3s_node:
                # Pending pods do not have a dispatchable Node Agent. Treat
                # them as missing so startup/reload waits or fails loudly
                # instead of routing intents to an empty agent address.
                continue
            self._node_of[node_id] = k3s_node

        missing = sorted(expected - set(self._node_of))
        if missing:
            raise RuntimeError("Missing active session pod location(s): " + ", ".join(missing[:20]))

        # Build agent addresses — NATS uses K8s node name as subject
        k3s_nodes = set(self._node_of.values())
        for k3s in k3s_nodes:
            if k3s:
                self._agent_addrs[k3s] = k3s  # Node name = NATS subject

        # Node IPs (InternalIP) are the VXLAN tunnel endpoints.
        for node in v1.list_node().items:
            name = node.metadata.name
            for addr in node.status.addresses or []:
                if addr.type == "InternalIP":
                    self._node_ips[name] = addr.address
                    break
        log.info(
            "Node IPs: %s",
            ", ".join(f"{n}={ip}" for n, ip in sorted(self._node_ips.items())),
        )

        log.info(
            "Loaded %d pods across %d K3s nodes from API",
            len(self._node_of),
            len(k3s_nodes),
        )

    def summary(self) -> str:
        """Human-readable summary for logging."""
        lines = []
        for k3s, addr in sorted(self._agent_addrs.items()):
            pods = sorted(nid for nid, n in self._node_of.items() if n == k3s)
            lines.append(f"  Node {k3s} -> agent {addr} ({len(pods)} pods)")
            for nid in pods[:5]:
                lines.append(f"    {nid}")
            if len(pods) > 5:
                lines.append(f"    ... and {len(pods) - 5} more")
        return "\n".join(lines)
