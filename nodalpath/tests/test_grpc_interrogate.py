"""Tests for nodalpath.push.grpc_interrogate — gRPC node interrogation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc

from nodalpath.models.almanac import ForwardingTable, IngressRule, LabelBinding
from nodalpath.models.inspection import BindingDiffKind
from nodalpath.proto import (
    Action,
    ForwardingTableState,
    IngressEntry,
    LabelEntry,
    NodeStatus,
)
from nodalpath.push.grpc_interrogate import (
    _diff_ingress,
    _diff_lsr,
    interrogate_node,
    interrogate_nodes,
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


def _make_observed_state(
    lsr_entries: list[LabelEntry] | None = None,
    ler_entries: list[IngressEntry] | None = None,
) -> ForwardingTableState:
    return ForwardingTableState(
        topology_state_id="state-1",
        sim_time="2026-03-01T14:30:00Z",
        lsr_entries=lsr_entries or [],
        ler_entries=ler_entries or [],
    )


# ---------------------------------------------------------------------------
# _diff_lsr
# ---------------------------------------------------------------------------


class TestDiffLsr:
    def test_missing_binding(self):
        planned = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            ]
        )
        observed = _make_observed_state(lsr_entries=[])
        diffs = _diff_lsr(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.MISSING
        assert diffs[0].in_label == 100

    def test_extra_binding(self):
        planned = _make_table(bindings=[])
        observed = _make_observed_state(
            lsr_entries=[
                LabelEntry(in_label=100, action=Action.SWAP, out_label=200, out_interface="isl0"),
            ]
        )
        diffs = _diff_lsr(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.EXTRA
        assert diffs[0].in_label == 100
        assert diffs[0].observed_action == "swap"

    def test_mismatch_binding(self):
        planned = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            ]
        )
        observed = _make_observed_state(
            lsr_entries=[
                LabelEntry(in_label=100, action=Action.SWAP, out_label=300, out_interface="isl0"),
            ]
        )
        diffs = _diff_lsr(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.MISMATCH
        assert diffs[0].planned_out_label == 200
        assert diffs[0].observed_out_label == 300

    def test_matching_binding_no_diff(self):
        planned = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            ]
        )
        observed = _make_observed_state(
            lsr_entries=[
                LabelEntry(in_label=100, action=Action.SWAP, out_label=200, out_interface="isl0"),
            ]
        )
        diffs = _diff_lsr(planned, observed)
        assert len(diffs) == 0

    def test_pop_binding_match(self):
        planned = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="pop", out_interface="gnd0"),
            ]
        )
        observed = _make_observed_state(
            lsr_entries=[
                LabelEntry(in_label=100, action=Action.POP, out_label=0, out_interface="gnd0"),
            ]
        )
        diffs = _diff_lsr(planned, observed)
        assert len(diffs) == 0


# ---------------------------------------------------------------------------
# _diff_ingress
# ---------------------------------------------------------------------------


class TestDiffIngress:
    def test_missing_ingress(self):
        planned = _make_table(
            rules=[
                IngressRule(dst_prefix="10.0.0.0/8", push_label=100, out_interface="gnd0"),
            ]
        )
        observed = _make_observed_state(ler_entries=[])
        diffs = _diff_ingress(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.MISSING
        assert diffs[0].dst_prefix == "10.0.0.0/8"

    def test_extra_ingress(self):
        planned = _make_table(rules=[])
        observed = _make_observed_state(
            ler_entries=[
                IngressEntry(dst_prefix="10.0.0.0/8", push_label=100, out_interface="gnd0"),
            ]
        )
        diffs = _diff_ingress(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.EXTRA

    def test_mismatch_ingress(self):
        planned = _make_table(
            rules=[
                IngressRule(dst_prefix="10.0.0.0/8", push_label=100, out_interface="gnd0"),
            ]
        )
        observed = _make_observed_state(
            ler_entries=[
                IngressEntry(dst_prefix="10.0.0.0/8", push_label=200, out_interface="gnd0"),
            ]
        )
        diffs = _diff_ingress(planned, observed)
        assert len(diffs) == 1
        assert diffs[0].kind == BindingDiffKind.MISMATCH
        assert diffs[0].planned_push_label == 100
        assert diffs[0].observed_push_label == 200


# ---------------------------------------------------------------------------
# interrogate_node
# ---------------------------------------------------------------------------


class TestInterrogateNode:
    @patch("nodalpath.push.grpc_interrogate.grpc")
    def test_success(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_grpc_mod.channel_ready_future.return_value = mock_future

        # Build mock stub
        mock_status = NodeStatus(
            node_id="sat-P00S00",
            current_topology_state_id="state-1",
            total_entries=2,
        )
        mock_fwd = ForwardingTableState(
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
            lsr_entries=[
                LabelEntry(in_label=100, action=Action.SWAP, out_label=200, out_interface="isl0"),
            ],
            ler_entries=[],
        )
        mock_stub_instance = MagicMock()
        mock_stub_instance.GetStatus.return_value = mock_status
        mock_stub_instance.GetForwardingTable.return_value = mock_fwd

        planned = _make_table(
            bindings=[
                LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
            ]
        )

        with patch(
            "nodalpath.push.grpc_interrogate.ForwardingServiceStub", return_value=mock_stub_instance
        ):
            result = interrogate_node("sat-P00S00", "10.42.0.10", "state-1", planned)

        assert result.reachable is True
        assert result.status_topology_state_id == "state-1"
        assert result.status_total_entries == 2
        assert result.has_deviation is False
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_interrogate.grpc")
    def test_timeout(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_future.result.side_effect = grpc.FutureTimeoutError()
        mock_grpc_mod.channel_ready_future.return_value = mock_future
        mock_grpc_mod.FutureTimeoutError = grpc.FutureTimeoutError
        mock_grpc_mod.RpcError = grpc.RpcError

        planned = _make_table()
        result = interrogate_node("sat-P00S00", "10.42.0.10", "state-1", planned)

        assert result.reachable is False
        assert "not ready" in result.error_message.lower()
        mock_channel.close.assert_called_once()

    @patch("nodalpath.push.grpc_interrogate.grpc")
    def test_rpc_error(self, mock_grpc_mod):
        mock_channel = MagicMock()
        mock_grpc_mod.insecure_channel.return_value = mock_channel
        mock_future = MagicMock()
        mock_grpc_mod.channel_ready_future.return_value = mock_future
        mock_grpc_mod.FutureTimeoutError = grpc.FutureTimeoutError
        mock_grpc_mod.RpcError = grpc.RpcError

        mock_stub_instance = MagicMock()
        mock_stub_instance.GetStatus.side_effect = grpc.RpcError()

        planned = _make_table()

        with patch(
            "nodalpath.push.grpc_interrogate.ForwardingServiceStub", return_value=mock_stub_instance
        ):
            result = interrogate_node("sat-P00S00", "10.42.0.10", "state-1", planned)

        assert result.reachable is False
        mock_channel.close.assert_called_once()


# ---------------------------------------------------------------------------
# interrogate_nodes (parallel)
# ---------------------------------------------------------------------------


class TestInterrogateNodes:
    def test_empty_returns_empty(self):
        assert interrogate_nodes([]) == []

    @patch("nodalpath.push.grpc_interrogate.interrogate_node")
    def test_preserves_order(self, mock_interrogate):
        from nodalpath.models.inspection import NodeInspectionResult

        mock_interrogate.side_effect = [
            NodeInspectionResult(node_id="a", reachable=True),
            NodeInspectionResult(node_id="b", reachable=True),
        ]
        table_a = _make_table(node_id="a")
        table_b = _make_table(node_id="b")
        results = interrogate_nodes(
            [
                ("a", "1.1.1.1", "state-1", table_a),
                ("b", "2.2.2.2", "state-1", table_b),
            ]
        )
        assert len(results) == 2
        assert results[0].node_id == "a"
        assert results[1].node_id == "b"
