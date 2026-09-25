"""Integration contracts for PodLocationMap loaded from Kubernetes.

These assertions protect the scheduler dispatch contract: every node of the
active session's wiring manifest is located from the canonical
``nodalarc.io/node-id`` label, and every located pod maps to a concrete
scheduler agent subject.
"""

from __future__ import annotations

import pytest
from nodalarc.runtime_naming import validate_runtime_node_id
from nodalarc.substrate.manifest_contract import (
    WIRING_MANIFEST_CONFIGMAP,
    decode_wiring_manifest,
)
from scheduler.pod_locator import PodLocationMap

pytestmark = pytest.mark.integration

NAMESPACE = "nodalarc"


def test_k8s_pod_location_map_locates_the_active_session_by_canonical_node_ids(
    k3s_available, monkeypatch
):
    import kubernetes.client
    import kubernetes.config

    # The Scheduler loads the in-cluster configuration; this test runs on a host
    # with a kubeconfig, which it loads in its place.
    kubernetes.config.load_kube_config()
    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    manifest = decode_wiring_manifest(
        kubernetes.client.CoreV1Api()
        .read_namespaced_config_map(WIRING_MANIFEST_CONFIGMAP, NAMESPACE)
        .data
    )

    loc = PodLocationMap()
    loc.load_from_k8s_api(
        namespace=NAMESPACE,
        expected_node_ids=set(manifest.nodes),
        session_id=manifest.session_id,
    )

    assert set(loc.node_ids) == set(manifest.nodes)
    assert loc.all_agent_addrs(), "scheduler cannot dispatch without agent subjects"

    for nid in sorted(loc.node_ids):
        validate_runtime_node_id(nid)

        k3s = loc.k3s_node(nid)
        assert k3s, f"{nid} is not assigned to a Kubernetes node"
        assert loc.agent_addr(nid) == k3s, f"{nid} dispatch agent must match host node name"

    located_nodes = {loc.k3s_node(nid) for nid in loc.node_ids}
    assert set(loc.all_agent_addrs()) == located_nodes
