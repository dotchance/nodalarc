"""Tests for node-images/nodalpath-fwd/fwd_server.py — gRPC ForwardingService.

Imports fwd_server via sys.path manipulation since it lives outside the
Python package. All kernel commands are mocked via subprocess.run.

Point-to-point veth links: MPLS routes use `dev {iface}` without `via`
nexthop, matching the NEBULA architecture for unnumbered p2p links.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the container directory to sys.path so we can import fwd_server
_CONTAINER_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "node-images" / "nodalpath-fwd"
)
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

import fwd_server  # noqa: E402
from proto import forwarding_pb2 as pb2  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset fwd_server module state between tests."""
    fwd_server._state["topology_state_id"] = ""
    fwd_server._state["sim_time"] = ""
    fwd_server._state["lsr_entries"] = {}
    fwd_server._state["ler_entries"] = {}
    fwd_server._state["last_update_ms"] = 0.0
    fwd_server._state["last_update_time"] = ""
    yield


def _make_lsr_entry(in_label=100, action=pb2.Action.SWAP, out_label=200, iface="isl0"):
    return pb2.LabelEntry(
        in_label=in_label,
        action=action,
        out_label=out_label,
        out_interface=iface,
    )


def _make_ler_entry(prefix="172.16.0.0/24", label=100, iface="gnd0"):
    return pb2.IngressEntry(
        dst_prefix=prefix,
        push_label=label,
        out_interface=iface,
    )


def _make_request(lsr=None, ler=None, state_id="state-1", sim_time="2026-03-01T14:30:00Z"):
    return pb2.ForwardingTableUpdate(
        topology_state_id=state_id,
        sim_time=sim_time,
        lsr_entries=lsr or [],
        ler_entries=ler or [],
    )


class TestApplyLsrCommands:
    @patch("fwd_server.subprocess.run")
    def test_swap_command(self, mock_run):
        entry = _make_lsr_entry(in_label=100, action=pb2.Action.SWAP, out_label=200, iface="isl0")
        fwd_server._install_lsr(entry)
        mock_run.assert_called_once_with(
            ["ip", "-f", "mpls", "route", "replace", "100", "as", "200", "dev", "isl0"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("fwd_server.subprocess.run")
    def test_pop_command(self, mock_run):
        entry = _make_lsr_entry(in_label=100, action=pb2.Action.POP, out_label=0, iface="gnd0")
        fwd_server._install_lsr(entry)
        mock_run.assert_called_once_with(
            ["ip", "-f", "mpls", "route", "replace", "100", "dev", "gnd0"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("fwd_server.subprocess.run")
    def test_ler_command(self, mock_run):
        entry = _make_ler_entry(prefix="172.16.0.0/24", label=100, iface="gnd0")
        fwd_server._install_ler(entry)
        mock_run.assert_called_once_with(
            ["ip", "route", "replace", "172.16.0.0/24", "encap", "mpls", "100", "dev", "gnd0"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("fwd_server.subprocess.run")
    def test_remove_lsr_command(self, mock_run):
        fwd_server._remove_lsr(100)
        mock_run.assert_called_once_with(
            ["ip", "-f", "mpls", "route", "del", "100"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("fwd_server.subprocess.run")
    def test_remove_ler_command(self, mock_run):
        fwd_server._remove_ler("172.16.0.0/24")
        mock_run.assert_called_once_with(
            ["ip", "route", "del", "172.16.0.0/24"],
            check=True,
            capture_output=True,
            text=True,
        )


class TestUpdateForwardingTable:
    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_update_success(self, mock_run, _):
        svc = fwd_server.ForwardingServiceImpl()
        request = _make_request(
            lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")],
            ler=[_make_ler_entry("172.16.0.0/24", 100, "gnd0")],
        )
        resp = svc.UpdateForwardingTable(request, None)
        assert resp.success is True
        assert resp.entries_installed == 2
        assert resp.apply_time_ms > 0

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_installs_before_removing(self, mock_run, _):
        """Verify install-before-remove ordering (NEBULA_3 atomicity)."""
        svc = fwd_server.ForwardingServiceImpl()

        # First: install entry with in_label=100
        req1 = _make_request(lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")])
        svc.UpdateForwardingTable(req1, None)
        mock_run.reset_mock()

        # Second: replace with in_label=101, removing 100
        req2 = _make_request(
            lsr=[_make_lsr_entry(101, pb2.Action.SWAP, 201, "isl0")],
            state_id="state-2",
        )
        svc.UpdateForwardingTable(req2, None)

        # install(101) should come before remove(100)
        calls = mock_run.call_args_list
        install_idx = None
        remove_idx = None
        for i, c in enumerate(calls):
            cmd = c[0][0]
            if "replace" in cmd and "101" in cmd:
                install_idx = i
            if "del" in cmd and "100" in cmd:
                remove_idx = i
        assert install_idx is not None
        assert remove_idx is not None
        assert install_idx < remove_idx

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_error_on_install_reports_failure(self, mock_run, _):
        """Errors on install are reported but don't abort the entire push."""
        svc = fwd_server.ForwardingServiceImpl()
        mock_run.side_effect = subprocess.CalledProcessError(1, "ip", stderr="RTNETLINK error")
        request = _make_request(
            lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")],
        )
        resp = svc.UpdateForwardingTable(request, None)
        assert resp.success is False
        assert "RTNETLINK" in resp.error_message

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_skip_down_interfaces(self, mock_run, mock_iface_up):
        """Entries for down interfaces are skipped, not failed."""
        mock_iface_up.side_effect = lambda iface: iface != "isl2"
        svc = fwd_server.ForwardingServiceImpl()
        request = _make_request(
            lsr=[
                _make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0"),
                _make_lsr_entry(101, pb2.Action.SWAP, 201, "isl2"),
            ],
        )
        resp = svc.UpdateForwardingTable(request, None)
        assert resp.success is True
        assert resp.entries_installed == 1  # only isl0 installed

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_incremental_only_changed(self, mock_run, _):
        """Only changed entries should be installed."""
        svc = fwd_server.ForwardingServiceImpl()

        entry_a = _make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")
        entry_b = _make_lsr_entry(101, pb2.Action.SWAP, 201, "isl0")
        req1 = _make_request(lsr=[entry_a, entry_b])
        svc.UpdateForwardingTable(req1, None)
        mock_run.reset_mock()

        # Second call: same entries, nothing should change
        req2 = _make_request(lsr=[entry_a, entry_b], state_id="state-2")
        resp = svc.UpdateForwardingTable(req2, None)
        assert resp.success is True
        assert resp.entries_installed == 0
        mock_run.assert_not_called()

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_full_replace(self, mock_run, _):
        """Full replacement: all old entries removed, all new entries installed."""
        svc = fwd_server.ForwardingServiceImpl()

        req1 = _make_request(lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")])
        svc.UpdateForwardingTable(req1, None)
        mock_run.reset_mock()

        # Replace with completely different entry
        req2 = _make_request(
            lsr=[_make_lsr_entry(101, pb2.Action.POP, 0, "gnd0")],
            state_id="state-2",
        )
        resp = svc.UpdateForwardingTable(req2, None)
        assert resp.success is True
        assert resp.entries_installed == 1  # installed 101
        # Should have called: install(101), remove(100)
        assert mock_run.call_count == 2


class TestGetForwardingTable:
    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_get_returns_current_state(self, mock_run, _):
        svc = fwd_server.ForwardingServiceImpl()
        request = _make_request(
            lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")],
            ler=[_make_ler_entry("172.16.0.0/24", 100, "gnd0")],
        )
        svc.UpdateForwardingTable(request, None)

        state = svc.GetForwardingTable(pb2.Empty(), None)
        assert state.topology_state_id == "state-1"
        assert len(state.lsr_entries) == 1
        assert len(state.ler_entries) == 1


class TestGetStatus:
    @patch("fwd_server.subprocess.run")
    def test_status_node_id(self, mock_run):
        fwd_server.NODE_ID = "sat-P00S00"
        svc = fwd_server.ForwardingServiceImpl()
        status = svc.GetStatus(pb2.Empty(), None)
        assert status.node_id == "sat-P00S00"
        fwd_server.NODE_ID = "unknown"  # Reset

    @patch("fwd_server._iface_up", return_value=True)
    @patch("fwd_server.subprocess.run")
    def test_status_topology_state_id(self, mock_run, _):
        svc = fwd_server.ForwardingServiceImpl()
        request = _make_request(
            lsr=[_make_lsr_entry(100, pb2.Action.SWAP, 200, "isl0")],
            state_id="topo-42",
        )
        svc.UpdateForwardingTable(request, None)
        status = svc.GetStatus(pb2.Empty(), None)
        assert status.current_topology_state_id == "topo-42"
        assert status.total_entries == 1


class TestIfaceUp:
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_missing_interface_returns_false(self, _):
        assert fwd_server._iface_up("nonexistent0") is False
