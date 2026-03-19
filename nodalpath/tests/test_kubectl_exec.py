"""Tests for nodalpath.push.kubectl_exec — deploy daemon Unix socket client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nodalpath.push.kubectl_exec import (
    ExecResult,
    exec_vtysh,
    node_id_to_pod_name,
    push_to_nodes,
)


def _make_mock_socket(response: dict) -> MagicMock:
    """Create a mock socket that returns a JSON response."""
    mock_sock = MagicMock()
    response_bytes = (json.dumps(response) + "\n").encode()
    mock_sock.recv.side_effect = [response_bytes, b""]
    return mock_sock


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
    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_success(self, mock_socket_cls):
        mock_sock = _make_mock_socket({"ok": True, "stdout": "ok\n", "stderr": "", "exit_code": 0})
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "ok\n"

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_failure(self, mock_socket_cls):
        mock_sock = _make_mock_socket(
            {"ok": False, "stdout": "", "stderr": "error\n", "exit_code": 1}
        )
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "bad command")
        assert result.success is False
        assert result.returncode == 1
        assert "error" in result.stderr

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_socket_not_found(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = FileNotFoundError("No such file")
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is False
        assert result.returncode == -1
        assert "socket not found" in result.stderr

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_connection_refused(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is False
        assert result.returncode == -1
        assert "connection refused" in result.stderr

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_timeout(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = TimeoutError("timed out")
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "slow command")
        assert result.success is False
        assert result.returncode == -1
        assert "Timeout" in result.stderr

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_request_contains_kubectl_exec_action(self, mock_socket_cls):
        mock_sock = _make_mock_socket({"ok": True, "stdout": "", "stderr": "", "exit_code": 0})
        mock_socket_cls.return_value = mock_sock
        exec_vtysh("sat-P00S00", "show ip route")
        sent_data = mock_sock.sendall.call_args[0][0].decode()
        req = json.loads(sent_data.strip())
        assert req["action"] == "kubectl_exec"
        assert req["pod"] == "sat-p00s00"
        assert req["container"] == "frr"
        assert req["command"] == ["vtysh", "-c", "show ip route"]

    @patch("nodalpath.push.kubectl_exec.socket.socket")
    def test_no_response_returns_failure(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b""  # EOF immediately
        mock_socket_cls.return_value = mock_sock
        result = exec_vtysh("sat-P00S00", "show ip route")
        assert result.success is False
        assert "No response" in result.stderr


# ---------------------------------------------------------------------------
# push_to_nodes
# ---------------------------------------------------------------------------


class TestPushToNodes:
    @patch("nodalpath.push.kubectl_exec.exec_vtysh")
    def test_all_tasks_submitted(self, mock_exec):
        mock_exec.return_value = ExecResult(
            node_id="x",
            pod_name="x",
            success=True,
            stdout="",
            stderr="",
            returncode=0,
        )
        tasks = [
            ("sat-P00S00", "cmd1"),
            ("sat-P00S01", "cmd2"),
            ("sat-P01S00", "cmd3"),
        ]
        results = push_to_nodes(tasks)
        assert len(results) == 3
        assert mock_exec.call_count == 3

    @patch("nodalpath.push.kubectl_exec.exec_vtysh")
    def test_results_in_input_order(self, mock_exec):
        def side_effect(node_id, commands, namespace=None, timeout=None):
            return ExecResult(
                node_id=node_id,
                pod_name=node_id.lower(),
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            )

        mock_exec.side_effect = side_effect
        tasks = [
            ("sat-P00S00", "cmd1"),
            ("sat-P01S01", "cmd2"),
        ]
        results = push_to_nodes(tasks)
        assert results[0].node_id == "sat-P00S00"
        assert results[1].node_id == "sat-P01S01"

    @patch("nodalpath.push.kubectl_exec.exec_vtysh")
    def test_one_failure_doesnt_prevent_others(self, mock_exec):
        def side_effect(node_id, commands, namespace=None, timeout=None):
            if node_id == "sat-P00S00":
                return ExecResult(
                    node_id=node_id,
                    pod_name="sat-p00s00",
                    success=False,
                    stdout="",
                    stderr="fail",
                    returncode=1,
                )
            return ExecResult(
                node_id=node_id,
                pod_name=node_id.lower(),
                success=True,
                stdout="ok",
                stderr="",
                returncode=0,
            )

        mock_exec.side_effect = side_effect
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
