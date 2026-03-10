"""Tests for gRPC transport path through the push scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nodalpath.models.almanac import (
    AlmanacEntry,
    ForwardingTable,
    IngressRule,
    LabelBinding,
)
from nodalpath.push.grpc_push import GrpcExecResult
from nodalpath.push.push_scheduler import PushScheduler, PushSchedulerConfig


def _make_entry(
    tables: list[ForwardingTable],
    state_id: str = "state-1",
    sim_time: str = "2026-03-01T14:30:00Z",
) -> AlmanacEntry:
    return AlmanacEntry(
        topology_state_id=state_id,
        sim_time=sim_time,
        forwarding_tables=tables,
        computed_paths=["path-1"],
        computation_time_ms=1.0,
    )


def _make_table(
    node_id: str,
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


_POD_IP_MAP = {
    "sat-P00S00": "10.42.0.10",
    "sat-P00S01": "10.42.0.11",
    "sat-P01S00": "10.42.0.12",
    "sat-P01S01": "10.42.0.13",
    "gs-alpha": "10.42.0.20",
    "gs-beta": "10.42.0.21",
}


class TestGrpcTransportConfig:
    def test_grpc_requires_pod_ip_map(self, simple_node_registry, simple_interface_map):
        config = PushSchedulerConfig(transport="grpc")
        with pytest.raises(ValueError, match="pod_ip_map"):
            PushScheduler(simple_node_registry, simple_interface_map, config=config)

    def test_vtysh_without_pod_ip_ok(self, simple_node_registry, simple_interface_map):
        config = PushSchedulerConfig(transport="vtysh")
        sched = PushScheduler(simple_node_registry, simple_interface_map, config=config)
        assert sched._pod_ip_map == {}


class TestGrpcPush:
    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_grpc_calls_grpc_push_not_vtysh(
        self, mock_grpc_push, mock_vtysh_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)
        mock_grpc_push.assert_called_once()
        mock_vtysh_push.assert_not_called()

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_vtysh_calls_vtysh_push_not_grpc(
        self, mock_grpc_push, mock_vtysh_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="vtysh")
        sched = PushScheduler(simple_node_registry, simple_interface_map, config=config)
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        from nodalpath.push.kubectl_exec import ExecResult
        mock_vtysh_push.return_value = [
            ExecResult("sat-P00S00", "sat-p00s00", True, "", "", 0),
        ]
        sched.push_entry(entry)
        mock_vtysh_push.assert_called_once()
        mock_grpc_push.assert_not_called()

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_pod_ip_used_in_grpc_task(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)
        tasks = mock_grpc_push.call_args[0][0]
        assert tasks[0][1] == "10.42.0.10"  # pod_ip

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_incremental_skip_no_changes(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc", use_incremental_diff=True)
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        # First push
        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)

        # Second push with same table → skipped
        mock_grpc_push.reset_mock()
        result = sched.push_entry(entry)
        mock_grpc_push.assert_not_called()
        assert result.nodes_skipped == 1

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    @patch("nodalpath.push.grpc_push.build_forwarding_update")
    def test_full_table_always_sent(
        self, mock_build, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        """Even with use_incremental_diff=True, gRPC sends full tables."""
        config = PushSchedulerConfig(transport="grpc", use_incremental_diff=True)
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        b1 = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = _make_table("sat-P00S00", bindings=[b1])
        e1 = _make_entry([t1], state_id="state-1")

        mock_update = MagicMock()
        mock_build.return_value = mock_update
        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(e1)

        # build_forwarding_update receives the full table, not a diff
        mock_build.assert_called_once()
        call_args = mock_build.call_args
        assert call_args[0][0] == t1  # full table object

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_installed_updated_on_success(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)
        assert "sat-P00S00" in sched._installed

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_not_updated_on_failure(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", False, 0, 0.0, "fail"),
        ]
        sched.push_entry(entry)
        assert "sat-P00S00" not in sched._installed

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_dry_run_grpc(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc", dry_run=True)
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        result = sched.push_entry(entry)
        mock_grpc_push.assert_not_called()
        assert result.nodes_succeeded == 1

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_topology_state_id_in_update(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table], state_id="topo-42")

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)
        tasks = mock_grpc_push.call_args[0][0]
        update = tasks[0][2]
        assert update.topology_state_id == "topo-42"

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_sim_time_in_update(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002, out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table], sim_time="2026-06-15T12:00:00Z")

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
        ]
        sched.push_entry(entry)
        tasks = mock_grpc_push.call_args[0][0]
        update = tasks[0][2]
        assert update.sim_time == "2026-06-15T12:00:00Z"

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_push_result_counts(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        b = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = _make_table("sat-P00S00", bindings=[b])
        t2 = _make_table("sat-P00S01", bindings=[b])
        empty = _make_table("sat-P01S00")
        entry = _make_entry([t1, t2, empty])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", True, 1, 0.5, ""),
            GrpcExecResult("sat-P00S01", "10.42.0.11", True, 1, 0.5, ""),
        ]
        result = sched.push_entry(entry)
        assert result.nodes_attempted == 2
        assert result.nodes_succeeded == 2
        assert result.nodes_skipped == 1
        assert result.nodes_failed == 0

    @patch("nodalpath.push.grpc_push.push_to_nodes_grpc")
    def test_failed_nodes(
        self, mock_grpc_push,
        simple_node_registry, simple_interface_map,
    ):
        config = PushSchedulerConfig(transport="grpc")
        sched = PushScheduler(
            simple_node_registry, simple_interface_map,
            config=config, pod_ip_map=_POD_IP_MAP,
        )
        b = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = _make_table("sat-P00S00", bindings=[b])
        entry = _make_entry([t1])

        mock_grpc_push.return_value = [
            GrpcExecResult("sat-P00S00", "10.42.0.10", False, 0, 0.0, "error"),
        ]
        result = sched.push_entry(entry)
        assert result.failed_nodes == ["sat-P00S00"]
        assert result.nodes_failed == 1
