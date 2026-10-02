"""Unit tests for vs_api.introspect — whitelist validation and vtysh execution."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from vs_api.introspect import run_vtysh

from tests.unit.test_workload_target import pod_document


@pytest.fixture(autouse=True)
def _mock_k8s_config():
    """Mock kubernetes config loading for all introspect tests."""
    with patch("vs_api.introspect.kubernetes.config.load_incluster_config"):
        yield


@pytest.fixture(autouse=True)
def _published_target():
    """Every node the tests name has one live pod publishing ``custom-router``."""

    def list_namespaced_pod(namespace, *, label_selector):
        node_id = label_selector.split("=", 1)[1]
        if node_id == "sat-p99s99":
            return SimpleNamespace(items=[])
        return SimpleNamespace(items=[pod_document(node_id, uid=f"uid-{node_id}")])

    with patch("vs_api.introspect.kubernetes.client.CoreV1Api") as core:
        core.return_value.list_namespaced_pod.side_effect = list_namespaced_pod
        yield


class TestWhitelist:
    """Command whitelist validation."""

    def test_arbitrary_command_rejected(self):
        with pytest.raises(ValueError, match="not in whitelist"):
            run_vtysh("sat-p00s00", "configure terminal")

    def test_partial_match_rejected(self):
        with pytest.raises(ValueError, match="not in whitelist"):
            run_vtysh("sat-p00s00", "show isis")

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError, match="not in whitelist"):
            run_vtysh("sat-p00s00", "")


class TestWorkloadTarget:
    """The exec lands on the pod and container the Operator published."""

    def test_node_id_must_be_a_runtime_identifier(self):
        with pytest.raises(ValueError, match="invalid node id"):
            run_vtysh("sat-P00S00", "show isis neighbor")


class TestNodeIdRequired:
    """Empty node_id raises ValueError."""

    def test_empty_node_id(self):
        with pytest.raises(ValueError, match="node_id is required"):
            run_vtysh("", "show isis neighbor")
