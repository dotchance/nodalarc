# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Pod location map — maps canonical node IDs to K3s nodes and PIDs.

IMPORTANT — case sensitivity contract:
  All node IDs are derived from AddressingScheme (e.g., "sat-P01S02").
  K8s pod names are lowercase ("sat-p01s02") and are NEVER used as node IDs.
  The K8s label "nodalarc.io/node-id" carries the canonical case, set by
  Helm at deploy time from the AddressingScheme output.

  This module reads the label value directly — it does not transform or
  derive node IDs from pod names. The canonical ID flows unchanged from
  the label into pid_map keys and gRPC message fields.

The Scheduler uses this to:
  1. Route BatchLinkDown/Up to the correct Node Agent (by K3s node)
  2. Pass PIDs to the Node Agent in gRPC messages
  3. Build interface_map keys using canonical node IDs
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class PodLocationMap:
    """Maps canonical node IDs to K3s node locations and PIDs.

    Built from K8s API (live) or from a pid_map.json file (na_deploy).
    """

    def __init__(self) -> None:
        # canonical_node_id -> k3s node name
        self._node_of: dict[str, str] = {}
        self._node_ips: dict[str, str] = {}  # k3s_node_name -> InternalIP
        # k3s node name -> Node Agent gRPC address
        self._agent_addrs: dict[str, str] = {}

    @property
    def node_ids(self) -> list[str]:
        """All canonical node IDs."""
        return list(self._node_of.keys())

    def k3s_node(self, node_id: str) -> str:
        """Get K3s node name for a canonical node ID."""
        return self._node_of.get(node_id, "")

    def agent_addr(self, node_id: str) -> str:
        """Get Node Agent gRPC address for the K3s node hosting this pod."""
        k3s = self._node_of.get(node_id, "")
        return self._agent_addrs.get(k3s, "")

    def node_ip(self, k3s_node: str) -> str:
        """Get the InternalIP for a K3s node. Empty string if unknown."""
        return self._node_ips.get(k3s_node, "")

    def all_agent_addrs(self) -> list[str]:
        """All unique Node Agent gRPC addresses."""
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

    def load_from_pid_map_file(self, path: str, agent_port: int = 50100) -> None:
        """Load from na_deploy's pid_map.json.

        The pid_map.json is keyed by canonical node IDs (from discover_pod_pids
        which reads the nodalarc.io/node-id label).

        For M4 (single K3s node), all pods are on the same node.
        We discover the node name from K8s API and set all pods to that node.
        """
        with open(path) as f:
            pid_map: dict[str, int] = json.load(f)

        # Discover K3s node name — all pods on same node in M4
        k3s_node = _discover_k3s_node()
        for nid in pid_map:
            self._node_of[nid] = k3s_node
        self._agent_addrs[k3s_node] = k3s_node

        log.info(
            "Loaded %d pods from pid_map, all on node %s, agent=%s",
            len(pid_map),
            k3s_node,
            self._agent_addrs[k3s_node],
        )

    def load_from_k8s_api(
        self,
        namespace: str | None = None,
        agent_port: int = 50100,
    ) -> None:
        """Load pod locations from K8s API.

        Reads canonical node IDs from nodalarc.io/node-id label and
        K3s node from pod.spec.nodeName. PIDs are NOT discovered here —
        the Node Agent owns PID resolution internally.

        Node Agent addresses are derived from the K3s node's InternalIP.
        """
        import kubernetes
        import kubernetes.client
        import kubernetes.config

        if namespace is None:
            from nodalarc.platform import get_platform_config

            namespace = get_platform_config().kubernetes_namespace

        try:
            kubernetes.config.load_incluster_config()
        except kubernetes.config.config_exception.ConfigException:
            kubernetes.config.load_kube_config()

        v1 = kubernetes.client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")

        for pod in pods.items:
            # Canonical node ID from label — NOT from pod.metadata.name
            node_id = pod.metadata.labels.get("nodalarc.io/node-id")
            if not node_id:
                continue

            k3s_node = pod.spec.node_name or ""
            self._node_of[node_id] = k3s_node

        # Build agent addresses — NATS uses K8s node name as subject
        k3s_nodes = set(self._node_of.values())
        for k3s in k3s_nodes:
            if k3s:
                self._agent_addrs[k3s] = k3s  # Node name = NATS subject

        # Discover node IPs (InternalIP) for VXLAN tunnel endpoints
        try:
            nodes = v1.list_node()
            for node in nodes.items:
                name = node.metadata.name
                for addr in node.status.addresses or []:
                    if addr.type == "InternalIP":
                        self._node_ips[name] = addr.address
                        break
            if self._node_ips:
                log.info(
                    "Node IPs: %s",
                    ", ".join(f"{n}={ip}" for n, ip in sorted(self._node_ips.items())),
                )
        except Exception as exc:
            log.warning("Failed to discover node IPs: %s", exc)

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


def _discover_k3s_node() -> str:
    """Discover the local K3s node name."""
    import socket

    return socket.gethostname()


def _k3s_node_ip(v1, node_name: str) -> str:
    """Get InternalIP for a K3s node."""
    try:
        node = v1.read_node(node_name)
        for addr in node.status.addresses:
            if addr.type == "InternalIP":
                return addr.address
    except Exception as exc:
        log.warning("Failed to get IP for node %s: %s", node_name, exc)
    return "127.0.0.1"
