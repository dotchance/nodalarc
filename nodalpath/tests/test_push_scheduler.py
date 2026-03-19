"""Tests for nodalpath.push.push_scheduler — synchronous push scheduling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nodalpath.models.almanac import (
    AlmanacEntry,
    ForwardingTable,
    IngressRule,
    LabelBinding,
)
from nodalpath.push.kubectl_exec import ExecResult
from nodalpath.push.push_scheduler import PushResult, PushScheduler, PushSchedulerConfig


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


# ---------------------------------------------------------------------------
# __init__ lookups
# ---------------------------------------------------------------------------


class TestPushSchedulerInit:
    def test_sid_to_loopback_populated(self, simple_node_registry, simple_interface_map):
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        assert sched._sid_to_loopback[16001] == "10.0.0.1"
        assert sched._sid_to_loopback[24000] == "10.255.0.1"

    def test_iface_to_peer_loopback_both_directions(
        self,
        simple_node_registry,
        simple_interface_map,
    ):
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        # (sat-P00S00, isl0) → sat-P00S01's loopback
        assert sched._iface_to_peer_loopback[("sat-P00S00", "isl0")] == "10.0.1.1"
        # (sat-P00S01, isl0) → sat-P00S00's loopback
        assert sched._iface_to_peer_loopback[("sat-P00S01", "isl0")] == "10.0.0.1"


# ---------------------------------------------------------------------------
# push_entry
# ---------------------------------------------------------------------------


class TestPushEntry:
    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_push_to_nodes_called_with_correct_count(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        sched.push_entry(entry)
        assert mock_push.call_count == 1
        tasks = mock_push.call_args[0][0]
        assert len(tasks) == 1
        assert tasks[0][0] == "sat-P00S00"

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_identical_entries_skips_push(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])

        # First push installs
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched.push_entry(entry)

        # Second push with identical table — should skip
        mock_push.reset_mock()
        result = sched.push_entry(entry)
        mock_push.assert_not_called()
        assert result.nodes_skipped == 1

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_dry_run_never_calls_push(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        config = PushSchedulerConfig(dry_run=True)
        sched = PushScheduler(simple_node_registry, simple_interface_map, config=config)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        result = sched.push_entry(entry)
        mock_push.assert_not_called()
        assert result.nodes_succeeded == 1

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_returns_push_result_with_correct_counts(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        empty_table = _make_table("sat-P00S01")  # no bindings → skipped
        entry = _make_entry([table, empty_table])
        result = sched.push_entry(entry)
        assert result.nodes_attempted == 1
        assert result.nodes_succeeded == 1
        assert result.nodes_skipped == 1
        assert result.nodes_failed == 0

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_success_updates_installed(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        sched.push_entry(entry)
        assert "sat-P00S00" in sched._installed
        assert sched._installed["sat-P00S00"] == table

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_failure_does_not_update_installed(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=False,
                stdout="",
                stderr="error",
                returncode=1,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        sched.push_entry(entry)
        assert "sat-P00S00" not in sched._installed

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_consecutive_push_diffs_against_installed(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        b1 = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = _make_table("sat-P00S00", bindings=[b1])
        e1 = _make_entry([t1], state_id="state-1")

        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched.push_entry(e1)

        # Second push with different binding
        b2 = LabelBinding(in_label=16001, action="swap", out_label=16003, out_interface="isl1")
        t2 = _make_table("sat-P00S00", bindings=[b2])
        e2 = _make_entry([t2], state_id="state-2")

        mock_push.reset_mock()
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched.push_entry(e2)

        # Should have pushed a diff (removal + addition)
        tasks = mock_push.call_args[0][0]
        commands = tasks[0][1]
        assert "no mpls lsp 16001" in commands
        assert "mpls lsp 16001" in commands

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_failed_nodes_list(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=False,
                stdout="",
                stderr="err",
                returncode=1,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        result = sched.push_entry(entry)
        assert result.failed_nodes == ["sat-P00S00"]

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_push_duration_ms_positive(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        entry = _make_entry([table])
        result = sched.push_entry(entry)
        assert result.push_duration_ms >= 0

    @patch("nodalpath.push.push_scheduler.push_to_nodes")
    def test_results_property(
        self,
        mock_push,
        simple_node_registry,
        simple_interface_map,
    ):
        mock_push.return_value = [
            ExecResult(
                node_id="sat-P00S00",
                pod_name="sat-p00s00",
                success=True,
                stdout="",
                stderr="",
                returncode=0,
            ),
        ]
        sched = PushScheduler(simple_node_registry, simple_interface_map)
        binding = LabelBinding(
            in_label=16001,
            action="swap",
            out_label=16002,
            out_interface="isl0",
        )
        table = _make_table("sat-P00S00", bindings=[binding])
        e1 = _make_entry([table], state_id="state-1")
        e2 = _make_entry([table], state_id="state-2")
        sched.push_entry(e1)
        # Second push skips because identical, but still creates a result
        sched.push_entry(e2)
        assert len(sched.results) == 2
        assert sched.results[0].topology_state_id == "state-1"
        assert sched.results[1].topology_state_id == "state-2"


# ---------------------------------------------------------------------------
# SlidingWindow integration
# ---------------------------------------------------------------------------


class TestSlidingWindowIntegration:
    def test_window_with_no_push_scheduler(
        self,
        synthetic_timeline_path,
        simple_node_registry,
        simple_interface_map,
        simple_prefix_map,
        simple_bandwidth_map,
    ):
        """Chunk 2 regression: SlidingWindow with push_scheduler=None runs fine."""
        from nodalpath.orchestrator.window import SlidingWindow

        window = SlidingWindow(
            timeline_path=synthetic_timeline_path,
            node_registry=simple_node_registry,
            interface_map=simple_interface_map,
            prefix_map=simple_prefix_map,
            bandwidth_map=simple_bandwidth_map,
        )
        transitions = window.process()
        assert transitions >= 1

    def test_window_with_push_scheduler(
        self,
        synthetic_timeline_path,
        simple_node_registry,
        simple_interface_map,
        simple_prefix_map,
        simple_bandwidth_map,
    ):
        """SlidingWindow calls push_entry once per transition when scheduler is set."""
        mock_scheduler = MagicMock()
        mock_scheduler.push_entry = MagicMock(
            return_value=PushResult(
                topology_state_id="test",
                sim_time="2026-03-01T14:30:00Z",
                nodes_attempted=0,
                nodes_succeeded=0,
                nodes_failed=0,
                nodes_skipped=0,
                push_duration_ms=0.0,
            )
        )

        from nodalpath.orchestrator.window import SlidingWindow

        window = SlidingWindow(
            timeline_path=synthetic_timeline_path,
            node_registry=simple_node_registry,
            interface_map=simple_interface_map,
            prefix_map=simple_prefix_map,
            bandwidth_map=simple_bandwidth_map,
            push_scheduler=mock_scheduler,
        )
        transitions = window.process()
        assert transitions >= 1
        assert mock_scheduler.push_entry.call_count == transitions
