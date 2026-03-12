"""Unit tests for vs_api.introspect — whitelist validation and vtysh execution."""

import socket
from unittest.mock import patch

import pytest

from vs_api.introspect import VTYSH_COMMANDS, run_vtysh
from nodalarc.platform import get_platform_config


class TestWhitelist:
    """Command whitelist validation."""

    def test_valid_commands_accepted(self):
        for cmd in VTYSH_COMMANDS:
            with patch("vs_api.introspect._daemon_request") as mock_req:
                mock_req.return_value = {
                    "ok": True,
                    "stdout": "ok",
                    "stderr": "",
                    "exit_code": 0,
                }
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

    @patch("vs_api.introspect._daemon_request")
    def test_uppercase_node_id_lowered(self, mock_req):
        mock_req.return_value = {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
        run_vtysh("sat-P00S00", "show isis neighbor")
        req_dict = mock_req.call_args[0][0]
        assert req_dict["pod"] == "sat-p00s00"

    @patch("vs_api.introspect._daemon_request")
    def test_already_lowercase_unchanged(self, mock_req):
        mock_req.return_value = {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
        run_vtysh("sat-p01s02", "show ip route")
        req_dict = mock_req.call_args[0][0]
        assert req_dict["pod"] == "sat-p01s02"


class TestNodeIdRequired:
    """Empty node_id raises ValueError."""

    def test_empty_node_id(self):
        with pytest.raises(ValueError, match="node_id is required"):
            run_vtysh("", "show isis neighbor")


class TestTimeout:
    """Timeout returns error dict."""

    @patch("vs_api.introspect._daemon_request")
    def test_timeout_returns_error(self, mock_req):
        mock_req.return_value = {"ok": False, "error": "Command timed out"}
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == -1
        assert result["error"] == "Command timed out"
        assert result["node_id"] == "sat-p00s00"
        assert result["command"] == "show isis neighbor"


class TestNonZeroExit:
    """Non-zero exit code returns stderr as error."""

    @patch("vs_api.introspect._daemon_request")
    def test_pod_not_found(self, mock_req):
        mock_req.return_value = {
            "ok": True,
            "stdout": "",
            "stderr": "error: pod sat-p99s99 not found",
            "exit_code": 1,
        }
        result = run_vtysh("sat-p99s99", "show isis neighbor")
        assert result["exit_code"] == 1
        assert "not found" in result["error"]

    @patch("vs_api.introspect._daemon_request")
    def test_success_has_no_error(self, mock_req):
        mock_req.return_value = {
            "ok": True,
            "stdout": "neighbor data",
            "stderr": "",
            "exit_code": 0,
        }
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == 0
        assert result["error"] is None
        assert result["output"] == "neighbor data"


class TestOutputTruncation:
    """Output truncated at 64KB."""

    @patch("vs_api.introspect._daemon_request")
    def test_large_output_truncated(self, mock_req):
        large_output = "x" * (get_platform_config().vs_api_introspect_max_response_bytes + 1000)
        mock_req.return_value = {
            "ok": True,
            "stdout": large_output,
            "stderr": "",
            "exit_code": 0,
        }
        result = run_vtysh("sat-p00s00", "show running-config")
        assert len(result["output"]) < len(large_output)
        assert result["output"].endswith("... (truncated)")

    @patch("vs_api.introspect._daemon_request")
    def test_small_output_not_truncated(self, mock_req):
        small_output = "some output"
        mock_req.return_value = {
            "ok": True,
            "stdout": small_output,
            "stderr": "",
            "exit_code": 0,
        }
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["output"] == small_output
