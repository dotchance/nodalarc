"""Unit tests for vs_api.introspect — whitelist validation and vtysh execution."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nodalarc.platform_config import get_platform_config
from vs_api.introspect import VTYSH_COMMANDS, run_vtysh

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

    def test_valid_commands_accepted(self):
        for cmd in VTYSH_COMMANDS:
            with patch("vs_api.introspect.kubernetes.stream.stream") as mock_stream:
                mock_stream.return_value = "ok"
                result = run_vtysh("sat-p00s00", cmd)
                assert result["exit_code"] == 0

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

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_exec_targets_the_published_pod_and_container(self, mock_stream):
        mock_stream.return_value = ""
        run_vtysh("sat-p01s02", "show ip route")
        call_args = mock_stream.call_args
        assert call_args[0][1] == "sat-p01s02"
        assert call_args[1]["container"] == "custom-router"

    def test_node_id_must_be_a_runtime_identifier(self):
        with pytest.raises(ValueError, match="invalid node id"):
            run_vtysh("sat-P00S00", "show isis neighbor")

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_missing_target_is_reported_before_any_exec(self, mock_stream):
        result = run_vtysh("sat-p99s99", "show isis neighbor")
        assert result["exit_code"] == -1
        assert result["error"].startswith("workload target unavailable: sat-p99s99:")
        assert "found 0" in result["error"]
        mock_stream.assert_not_called()


class TestNodeIdRequired:
    """Empty node_id raises ValueError."""

    def test_empty_node_id(self):
        with pytest.raises(ValueError, match="node_id is required"):
            run_vtysh("", "show isis neighbor")


class TestExecErrors:
    """K8s exec errors return error dict."""

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_exec_exception_returns_error(self, mock_stream):
        mock_stream.side_effect = Exception("connection timeout")
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == -1
        assert result["error"] == "vtysh exec failed"
        assert "connection timeout" not in result["error"]
        assert result["node_id"] == "sat-p00s00"


class TestNonZeroExit:
    """Exec results."""

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_pod_not_found(self, mock_stream):
        import kubernetes.client.rest

        mock_stream.side_effect = kubernetes.client.rest.ApiException(
            status=404, reason="Not Found"
        )
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == -1
        assert result["error"] == "Kubernetes exec failed"
        assert "Not Found" not in result["error"]

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_success_has_no_error(self, mock_stream):
        mock_stream.return_value = "neighbor data"
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == 0
        assert result["error"] is None
        assert result["output"] == "neighbor data"


class TestOutputTruncation:
    """Output truncated at configured max."""

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_large_output_truncated(self, mock_stream):
        large_output = "x" * (get_platform_config().vs_api_introspect_max_response_bytes + 1000)
        mock_stream.return_value = large_output
        result = run_vtysh("sat-p00s00", "show running-config")
        assert len(result["output"]) < len(large_output)
        assert result["output"].endswith("... (truncated)")

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_small_output_not_truncated(self, mock_stream):
        mock_stream.return_value = "some output"
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["output"] == "some output"
