"""Unit tests for vs_api.introspect — whitelist validation and vtysh execution."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from vs_api.introspect import VTYSH_COMMANDS, run_vtysh, _MAX_OUTPUT_BYTES


class TestWhitelist:
    """Command whitelist validation."""

    def test_valid_commands_accepted(self):
        for cmd in VTYSH_COMMANDS:
            # Should not raise
            with patch("vs_api.introspect.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="ok", stderr="", returncode=0,
                )
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

    @patch("vs_api.introspect.subprocess.run")
    def test_uppercase_node_id_lowered(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        run_vtysh("sat-P00S00", "show isis neighbor")
        args = mock_run.call_args[0][0]
        assert args[4] == "sat-p00s00"  # pod_name position in kubectl exec args

    @patch("vs_api.introspect.subprocess.run")
    def test_already_lowercase_unchanged(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        run_vtysh("sat-p01s02", "show ip route")
        args = mock_run.call_args[0][0]
        assert args[4] == "sat-p01s02"


class TestNodeIdRequired:
    """Empty node_id raises ValueError."""

    def test_empty_node_id(self):
        with pytest.raises(ValueError, match="node_id is required"):
            run_vtysh("", "show isis neighbor")


class TestTimeout:
    """Timeout returns error dict."""

    @patch("vs_api.introspect.subprocess.run")
    def test_timeout_returns_error(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=15)
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == -1
        assert result["error"] == "Command timed out"
        assert result["node_id"] == "sat-p00s00"
        assert result["command"] == "show isis neighbor"


class TestNonZeroExit:
    """Non-zero exit code returns stderr as error."""

    @patch("vs_api.introspect.subprocess.run")
    def test_pod_not_found(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="error: pod sat-p99s99 not found",
            returncode=1,
        )
        result = run_vtysh("sat-p99s99", "show isis neighbor")
        assert result["exit_code"] == 1
        assert "not found" in result["error"]

    @patch("vs_api.introspect.subprocess.run")
    def test_success_has_no_error(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="neighbor data",
            stderr="",
            returncode=0,
        )
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["exit_code"] == 0
        assert result["error"] is None
        assert result["output"] == "neighbor data"


class TestOutputTruncation:
    """Output truncated at 64KB."""

    @patch("vs_api.introspect.subprocess.run")
    def test_large_output_truncated(self, mock_run):
        large_output = "x" * (_MAX_OUTPUT_BYTES + 1000)
        mock_run.return_value = MagicMock(
            stdout=large_output, stderr="", returncode=0,
        )
        result = run_vtysh("sat-p00s00", "show running-config")
        assert len(result["output"]) < len(large_output)
        assert result["output"].endswith("... (truncated at 64KB)")

    @patch("vs_api.introspect.subprocess.run")
    def test_small_output_not_truncated(self, mock_run):
        small_output = "some output"
        mock_run.return_value = MagicMock(
            stdout=small_output, stderr="", returncode=0,
        )
        result = run_vtysh("sat-p00s00", "show isis neighbor")
        assert result["output"] == small_output
