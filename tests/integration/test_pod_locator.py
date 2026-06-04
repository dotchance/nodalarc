"""Integration contracts for PodLocationMap loaded from Kubernetes.

These assertions protect the scheduler dispatch contract: node IDs must come
from the canonical ``nodalarc.io/node-id`` label, and every located pod must map
to a concrete scheduler agent subject.
"""

from __future__ import annotations

import pytest
from nodalarc.runtime_naming import validate_runtime_node_id
from scheduler.pod_locator import PodLocationMap

pytestmark = pytest.mark.integration


def test_k8s_pod_location_map_preserves_canonical_node_ids(k3s_available):
    loc = PodLocationMap()
    loc.load_from_k8s_api(namespace="nodalarc")

    assert loc.node_ids, "expected at least one deployed NodalArc pod"
    assert loc.all_agent_addrs(), "scheduler cannot dispatch without agent subjects"

    for nid in sorted(loc.node_ids):
        validate_runtime_node_id(nid)

        k3s = loc.k3s_node(nid)
        assert k3s, f"{nid} is not assigned to a Kubernetes node"
        assert loc.agent_addr(nid) == k3s, f"{nid} dispatch agent must match host node name"

    located_nodes = {loc.k3s_node(nid) for nid in loc.node_ids}
    assert set(loc.all_agent_addrs()) == located_nodes
