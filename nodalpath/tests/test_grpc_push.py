"""Tests for nodalpath.push.grpc_push — gRPC transport client."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import grpc
import pytest

from nodalpath.models.almanac import ForwardingTable, IngressRule, LabelBinding
from nodalpath.proto import Action, ForwardingTableUpdate, PushResponse
from nodalpath.push.grpc_push import (
    GrpcExecResult,
    build_forwarding_update,
    push_forwarding_table,
    push_to_nodes_grpc,
)


def _make_table(
    node_id: str = "sat-P00S00",
    bindings: list[LabelBinding] | None = None,
    rules: list[IngressRule] | None = None,
) -> ForwardingTable:
    return ForwardingTable(
        node_id=node_id,
        topology_state_id="state-1",
        sim_time="2026-03-01T14:30:00Z",
        lsr_bindings=bindings or [],
        ler_ingress_rules=rules or [],
    )


# ---------------------------------------------------------------------------
# build_forwarding_update
# ---------------------------------------------------------------------------


class TestBuildForwardingUpdate:
    def test_swap_produces_swap_action(self):
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table(bindings=[binding])
        update = build_forwarding_update(table, "state-1", "2026-03-01T14:30:00Z")
        assert len(update.lsr_entries) == 1
        entry = update.lsr_entries[0]
        assert entry.action == Action.SWAP
        assert entry.in_label == 16001
        assert entry.out_label == 16002
        assert entry.out_interface == "isl0"

    def test_pop_produces_pop_action_with_zero_out_label(self):
        binding = LabelBinding(
            in_label=16001,
            action="pop",
            out_label=None,
            out_interface="gnd0",
        )
        table = _make_table(bindings=[binding])
        update = build_forwarding_update(table, "state-1", "2026-03-01T14:30:00Z")
        assert len(update.lsr_entries) == 1
        entry = update.lsr_entries[0]
        assert entry.action == Action.POP
        assert entry.out_label == 0

    def test_push_action_skipped_with_warning(self, caplog):
        binding = LabelBinding(
            in_label=16001,
            action="push",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table(bindings=[binding])
        with caplog.at_level(logging.WARNING):
            update = build_forwarding_update(table, "state-1", "2026-03-01T14:30:00Z")
        assert len(update.lsr_entries) == 0
        assert "push action" in caplog.text.lower() or "Skipping push" in caplog.text

    def test_ingress_rule_mapped_to_ingress_entry(self):
        rule = IngressRule(
            dst_prefix="172.16.0.0/24",
            push_label=16001,
            out_interface="gnd0",
        )
        table = _make_table(rules=[rule])
        update = build_forwarding_update(table, "state-1", "2026-03-01T14:30:00Z")
        assert len(update.ler_entries) == 1
        entry = update.ler_entries[0]
        assert entry.dst_prefix == "172.16.0.0/24"
        assert entry.push_label == 16001
        assert entry.out_interface == "gnd0"

    def test_topology_state_id_and_sim_time_propagated(self):
        table = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            ]
        )
        update = build_forwarding_update(table, "topo-42", "2026-06-15T12:00:00Z")
        assert update.topology_state_id == "topo-42"
        assert update.sim_time == "2026-06-15T12:00:00Z"

    def test_backup_fields_zeroed_when_none(self):
        binding = LabelBinding(
            in_label=100,
            action="swap",
            out_label=200,
            out_interface="isl0",
            backup_out_label=None,
            backup_out_interface=None,
        )
        rule = IngressRule(
            dst_prefix="10.0.0.0/8",
            push_label=100,
            out_interface="gnd0",
            backup_push_label=None,
            backup_out_interface=None,
        )
        table = _make_table(bindings=[binding], rules=[rule])
        update = build_forwarding_update(table, "s", "t")
        assert update.lsr_entries[0].backup_out_label == 0
        assert update.lsr_entries[0].backup_out_interface == ""
        assert update.ler_entries[0].backup_push_label == 0
        assert update.ler_entries[0].backup_out_interface == ""

    def test_multiple_entries_preserved(self):
        bindings = [
            LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            LabelBinding(in_label=101, action="pop", out_interface="gnd0"),
        ]
        rules = [
            IngressRule(dst_prefix="10.0.0.0/8", push_label=100, out_interface="gnd0"),
            IngressRule(dst_prefix="10.1.0.0/8", push_label=101, out_interface="gnd0"),
        ]
        table = _make_table(bindings=bindings, rules=rules)
        update = build_forwarding_update(table, "s", "t")
        assert len(update.lsr_entries) == 2
        assert len(update.ler_entries) == 2


# ---------------------------------------------------------------------------
# push_forwarding_table
# ---------------------------------------------------------------------------


class TestPushForwardingTable:
    def _make_update(self) -> ForwardingTableUpdate:
        return ForwardingTableUpdate(
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
        )

    @patch("nodalpath.push.grpc_push.grpc")
    def test_success(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel

        mock_future = MagicMock()
        mock_grpc_mod.channel_ready_future.return_value = mock_future

        # Stub service responds
        mock_stub_cls = MagicMock()
        mock_response = PushResponse(
            success=True,
            error_message="",
            entries_installed=5,
            apply_time_ms=1.5,
        )
        mock_stub_instance = MagicMock()
        mock_stub_instance.UpdateForwardingTable.return_value = mock_response

        with patch(
            "nodalpath.push.grpc_push.ForwardingServiceStub", return_value=mock_stub_instance
        ):
            result = push_forwarding_table("sat-P00S00", "10.42.0.10", self._make_update())

        assert result.success is True
        assert result.entries_installed == 5
        assert result.apply_time_ms == pytest.approx(1.5)
        assert result.error_message == ""
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_push.grpc")
    def test_rpc_error_captured(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_grpc_mod.channel_ready_future.return_value = mock_future

        mock_stub_instance = MagicMock()
        mock_stub_instance.UpdateForwardingTable.side_effect = grpc.RpcError()

        # Need RpcError to be recognized as the right type
        mock_grpc_mod.RpcError = grpc.RpcError
        mock_grpc_mod.FutureTimeoutError = grpc.FutureTimeoutError

        with patch(
            "nodalpath.push.grpc_push.ForwardingServiceStub", return_value=mock_stub_instance
        ):
            result = push_forwarding_table("sat-P00S00", "10.42.0.10", self._make_update())

        assert result.success is False
        assert result.node_id == "sat-P00S00"
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_push.grpc")
    def test_channel_timeout_captured(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_future.result.side_effect = grpc.FutureTimeoutError()
        mock_grpc_mod.channel_ready_future.return_value = mock_future
        mock_grpc_mod.FutureTimeoutError = grpc.FutureTimeoutError
        mock_grpc_mod.RpcError = grpc.RpcError

        result = push_forwarding_table("sat-P00S00", "10.42.0.10", self._make_update())

        assert result.success is False
        assert "not ready" in result.error_message.lower() or "Channel" in result.error_message
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_push.grpc")
    def test_generic_exception_captured(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_future.result.side_effect = RuntimeError("boom")
        mock_grpc_mod.channel_ready_future.return_value = mock_future
        mock_grpc_mod.FutureTimeoutError = grpc.FutureTimeoutError
        mock_grpc_mod.RpcError = grpc.RpcError

        result = push_forwarding_table("sat-P00S00", "10.42.0.10", self._make_update())

        assert result.success is False
        assert "boom" in result.error_message
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_push.grpc")
    def test_response_success_false(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_grpc_mod.channel_ready_future.return_value = mock_future

        mock_response = PushResponse(
            success=False,
            error_message="route conflict",
            entries_installed=0,
            apply_time_ms=0.0,
        )
        mock_stub_instance = MagicMock()
        mock_stub_instance.UpdateForwardingTable.return_value = mock_response

        with patch(
            "nodalpath.push.grpc_push.ForwardingServiceStub", return_value=mock_stub_instance
        ):
            result = push_forwarding_table("sat-P00S00", "10.42.0.10", self._make_update())

        assert result.success is False
        assert result.error_message == "route conflict"


# ---------------------------------------------------------------------------
# push_to_nodes_grpc
# ---------------------------------------------------------------------------


class TestPushToNodesGrpc:
    def test_empty_returns_empty(self):
        assert push_to_nodes_grpc([]) == []

    @patch("nodalpath.push.grpc_push.push_forwarding_table")
    def test_preserves_order(self, mock_push):
        mock_push.side_effect = [
            GrpcExecResult("a", "1.1.1.1", True, 5, 1.0, ""),
            GrpcExecResult("b", "2.2.2.2", True, 3, 0.5, ""),
        ]
        update = ForwardingTableUpdate(topology_state_id="s", sim_time="t")
        results = push_to_nodes_grpc([("a", "1.1.1.1", update), ("b", "2.2.2.2", update)])
        assert len(results) == 2
        assert results[0].node_id == "a"
        assert results[1].node_id == "b"

    @patch("nodalpath.push.grpc_push.push_forwarding_table")
    def test_partial_failure(self, mock_push):
        mock_push.side_effect = [
            GrpcExecResult("a", "1.1.1.1", True, 5, 1.0, ""),
            GrpcExecResult("b", "2.2.2.2", False, 0, 0.0, "error"),
        ]
        update = ForwardingTableUpdate(topology_state_id="s", sim_time="t")
        results = push_to_nodes_grpc([("a", "1.1.1.1", update), ("b", "2.2.2.2", update)])
        assert results[0].success is True
        assert results[1].success is False

    @patch("nodalpath.push.grpc_push.push_forwarding_table")
    def test_max_workers_respected(self, mock_push):
        mock_push.return_value = GrpcExecResult("a", "1.1.1.1", True, 1, 0.1, "")
        update = ForwardingTableUpdate(topology_state_id="s", sim_time="t")
        tasks = [("a", "1.1.1.1", update)] * 5
        results = push_to_nodes_grpc(tasks, max_workers=2)
        assert len(results) == 5
        assert all(r.success for r in results)
