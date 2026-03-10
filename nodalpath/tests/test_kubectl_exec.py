"""Tests for nodalpath.push.kubectl_exec — subprocess kubectl exec wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nodalpath.push.kubectl_exec import (
    DEFAULT_NAMESPACE,
    KUBECONFIG,
    ExecResult,
    exec_vtysh,
    node_id_to_pod_name,
    push_to_nodes,
)


# ---------------------------------------------------------------------------
# node_id_to_pod_name
# ---------------------------------------------------------------------------


class TestNodeIdToPodName:
    def test_satellite_uppercased(self):
        assert node_id_to_pod_name("sat-P02S05") == "sat-p02s05"

    def test_ground_station_already_lowercase(self):
        assert node_id_to_pod_name("gs-hawthorne") == "gs-hawthorne"


# ---------------------------------------------------------------------------
# exec_vtysh
# ---------------------------------------------------------------------------


class TestExecVtysh:
    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr="",
        )
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "ok\n"

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error\n",
        )
        result = exec_vtysh("sat-P00S00", "bad command")
        assert result.success is False
        assert result.returncode == 1
        assert "error" in result.stderr

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=10)
        result = exec_vtysh("sat-P00S00", "slow command")
        assert result.success is False
        assert result.returncode == -1
        assert "timeout" in result.stderr

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("kubectl not found")
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is False
        assert result.returncode == -1
        assert "kubectl not found" in result.stderr

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_command_structure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        exec_vtysh("sat-P00S00", "show ip route", namespace="test-ns")
        args = mock_run.call_args
        cmd = args[0][0]
        assert cmd[0] == "kubectl"
        assert cmd[1] == "exec"
        assert "-n" in cmd
        ns_idx = cmd.index("-n")
        assert cmd[ns_idx + 1] == "test-ns"
        assert "sat-p00s00" in cmd  # pod name
        assert "-c" in cmd
        c_idx = cmd.index("-c", ns_idx + 2)  # -c after pod name
        assert cmd[c_idx + 1] == "frr"
        assert "--" in cmd
        assert "vtysh" in cmd
        # vtysh -c <commands>
        vtysh_idx = cmd.index("vtysh")
        assert cmd[vtysh_idx + 1] == "-c"
        assert cmd[vtysh_idx + 2] == "show ip route"

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_env_has_kubeconfig(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        exec_vtysh("sat-P00S00", "show ip route")
        args = mock_run.call_args
        env = args[1].get("env") or args.kwargs.get("env")
        assert env["KUBECONFIG"] == KUBECONFIG

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_commands_as_single_arg(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        commands = "configure terminal\n mpls lsp 16001 10.0.0.2 16002\nend\nwrite memory"
        exec_vtysh("sat-P00S00", commands)
        cmd = mock_run.call_args[0][0]
        # The commands string should appear as a single argument after vtysh -c
        vtysh_idx = cmd.index("vtysh")
        assert cmd[vtysh_idx + 2] == commands


# ---------------------------------------------------------------------------
# push_to_nodes
# ---------------------------------------------------------------------------


class TestPushToNodes:
    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_all_tasks_submitted(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        tasks = [
            ("sat-P00S00", "cmd1"),
            ("sat-P00S01", "cmd2"),
            ("sat-P01S00", "cmd3"),
        ]
        results = push_to_nodes(tasks)
        assert len(results) == 3
        assert mock_run.call_count == 3

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_results_in_input_order(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        tasks = [
            ("sat-P00S00", "cmd1"),
            ("sat-P01S01", "cmd2"),
        ]
        results = push_to_nodes(tasks)
        assert results[0].node_id == "sat-P00S00"
        assert results[1].node_id == "sat-P01S01"

    @patch("nodalpath.push.kubectl_exec.subprocess.run")
    def test_one_failure_doesnt_prevent_others(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            pod = cmd[4]  # pod name is after -n namespace
            if pod == "sat-p00s00":
                return MagicMock(returncode=1, stdout="", stderr="fail")
            return MagicMock(returncode=0, stdout="ok", stderr="")

        mock_run.side_effect = side_effect
        tasks = [
            ("sat-P00S00", "cmd1"),
            ("sat-P00S01", "cmd2"),
        ]
        results = push_to_nodes(tasks)
        assert results[0].success is False
        assert results[1].success is True

    def test_empty_list_returns_empty(self):
        results = push_to_nodes([])
        assert results == []
