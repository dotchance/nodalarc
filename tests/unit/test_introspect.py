"""Unit tests for vs_api.introspect — whitelist validation and vtysh execution."""

from unittest.mock import patch

import pytest
from nodalarc.platform import get_platform_config

from vs_api.introspect import VTYSH_COMMANDS, run_vtysh


@pytest.fixture(autouse=True)
def _mock_k8s_config():
    """Mock kubernetes config loading for all introspect tests."""
    with patch("vs_api.introspect.kubernetes.config.load_incluster_config"):
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


class TestPodNameDerivation:
    """Pod name is node_id lowercased."""

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_uppercase_node_id_lowered(self, mock_stream):
        mock_stream.return_value = ""
        run_vtysh("sat-P00S00", "show isis neighbor")
        call_args = mock_stream.call_args
        assert call_args[0][1] == "sat-p00s00"

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_already_lowercase_unchanged(self, mock_stream):
        mock_stream.return_value = ""
        run_vtysh("sat-p01s02", "show ip route")
        call_args = mock_stream.call_args
        assert call_args[0][1] == "sat-p01s02"


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
        assert "connection timeout" in result["error"]
        assert result["node_id"] == "sat-p00s00"


class TestNonZeroExit:
    """Exec results."""

    @patch("vs_api.introspect.kubernetes.stream.stream")
    def test_pod_not_found(self, mock_stream):
        import kubernetes.client.rest

        mock_stream.side_effect = kubernetes.client.rest.ApiException(
            status=404, reason="Not Found"
        )
        result = run_vtysh("sat-p99s99", "show isis neighbor")
        assert result["exit_code"] == -1
        assert "Not Found" in result["error"]

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
