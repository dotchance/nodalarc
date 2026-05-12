"""Node Agent handler tests — call handlers directly, no transport.

Tests handler logic:
- Per-interface locality (LOCAL/CROSS_NODE)
- Empty batches succeed
- Bad PIDs return structured errors
- None pid_map raises ValueError (wiring never happened)
"""

from __future__ import annotations

import pytest
from nodalarc.proto import node_agent_pb2
from node_agent.command_contract import RuntimeFence
from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_set_latency,
)

# All tests pass pid_map={} — an initialized but empty map.
# This represents a node where wiring completed but no session pods
# are scheduled. pid_map=None means wiring never happened and is
# rejected by the handler (ValueError).
EMPTY_PID_MAP: dict[str, int] = {}
FENCE = RuntimeFence(session_id="demo", wiring_generation="sha256:" + "a" * 64)


def _env(kind: str, op: str) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id=op,
        session_id=FENCE.session_id,
        wiring_generation=FENCE.wiring_generation,
        operation_kind=kind,
    )


class TestBatchLinkDown:
    def test_cross_node_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(envelope=_env("BatchLinkDown", "test-cross-down"))
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_downed == 0

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(envelope=_env("BatchLinkDown", "test-empty-down"))
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_downed == 0
        assert resp.error_message == ""

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=_env("BatchLinkDown", "test-bad-pid"),
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
            ],
        )
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert resp.error_message != ""
        assert len(resp.interface_results) == 1
        assert resp.interface_results[0].node_id == "sat-P00S00"
        assert resp.interface_results[0].interface_name == "isl0"
        assert resp.interface_results[0].success is False

    def test_multiple_links_one_fails(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=_env("BatchLinkDown", "test-partial"),
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S01",
                    interface_name="isl1",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S00",
                    peer_interface_name="isl0",
                ),
            ],
        )
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert len(resp.interface_results) == 2
        assert {r.interface_name for r in resp.interface_results} == {"isl0", "isl1"}
        assert all(not r.success for r in resp.interface_results)

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkDownRequest(envelope=_env("BatchLinkDown", "test-none"))
        with pytest.raises(ValueError, match="pid_map is None"):
            handle_batch_link_down(req, pid_map=None, fence=FENCE)

    def test_cross_node_ground_cleanup_failure_marks_dirty(self, monkeypatch):
        from node_agent import vxlan

        def _fail_cleanup(*_args, **_kwargs):
            raise RuntimeError("cleanup failed")

        monkeypatch.setattr(vxlan, "detach_cross_node_ground", _fail_cleanup)
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=_env("BatchLinkDown", "test-cross-ground-cleanup-dirty"),
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="gnd0",
                    link_type=node_agent_pb2.LINK_TYPE_GROUND,
                    locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                    gs_id="gs-den",
                    sat_id="sat-P00S00",
                    peer_node_id="gs-den",
                    peer_interface_name="term0",
                    remote_node_ip="10.0.0.2",
                    vni=1001,
                )
            ],
        )

        resp = handle_batch_link_down(req, pid_map={"sat-P00S00": 1234}, fence=FENCE)

        assert resp.success is False
        assert resp.dirty_kernel is True
        assert resp.interface_results[0].dirty_kernel is True
        assert resp.interface_results[0].error_code == node_agent_pb2.NODE_AGENT_CLEANUP_FAILED


class TestBatchLinkUp:
    def test_cross_node_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(envelope=_env("BatchLinkUp", "test-cross-up"))
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(envelope=_env("BatchLinkUp", "test-empty-up"))
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=_env("BatchLinkUp", "test-bad-pid-up"),
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
            ],
        )
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.error_message != ""
        assert len(resp.interface_results) == 1
        assert resp.interface_results[0].node_id == "sat-P00S00"
        assert resp.interface_results[0].interface_name == "isl0"
        assert resp.interface_results[0].success is False

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkUpRequest(envelope=_env("BatchLinkUp", "test-none"))
        with pytest.raises(ValueError, match="pid_map is None"):
            handle_batch_link_up(req, pid_map=None, fence=FENCE)

    def test_cross_node_ground_applies_and_verifies_local_shaping(self, monkeypatch):
        from node_agent import handlers, kernel_verifier, namespace_ops, substrate_monitor, vxlan

        calls: list[tuple] = []
        monkeypatch.setenv("HOST_IP", "10.0.0.1")
        monkeypatch.setattr(handlers, "_local_ip", None)
        monkeypatch.setattr(
            vxlan,
            "attach_cross_node_ground",
            lambda **kwargs: calls.append(("attach", kwargs)),
        )
        monkeypatch.setattr(
            namespace_ops,
            "apply_link_shaping",
            lambda pid, ifname, latency, bandwidth: calls.append(
                ("shape", pid, ifname, latency, bandwidth)
            ),
        )
        monkeypatch.setattr(
            substrate_monitor,
            "add_peer_ref",
            lambda ref: calls.append(("peer_ref", ref.remote_ip, ref.vni, ref.local_ifname)),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_vxlan",
            lambda vni, *, local_ip, remote_ip: kernel_verifier.Proof.ok("vxlan"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_mirred",
            lambda src, dst: kernel_verifier.Proof.ok(f"mirred {src}->{dst}"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_qdisc",
            lambda pid, ifname, *, delay_ms, rate_mbps=None: kernel_verifier.Proof.ok("qdisc"),
        )

        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=_env("BatchLinkUp", "test-cross-ground-shape"),
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="gnd0",
                    link_type=node_agent_pb2.LINK_TYPE_GROUND,
                    locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                    latency_ms=4.5,
                    bandwidth_mbps=100.0,
                    gs_id="gs-den",
                    sat_id="sat-P00S00",
                    peer_node_id="gs-den",
                    peer_interface_name="term0",
                    remote_node_ip="10.0.0.2",
                    vni=1001,
                )
            ],
        )

        resp = handle_batch_link_up(req, pid_map={"sat-P00S00": 1234}, fence=FENCE)

        assert resp.success is True
        assert resp.interface_results[0].verified is True
        assert ("shape", 1234, "gnd0", 4.5, 100.0) in calls
        assert ("peer_ref", "10.0.0.2", 1001, "gnd0") in calls


class TestSetLatency:
    def test_empty_request_succeeds(self):
        req = node_agent_pb2.SetLatencyRequest(envelope=_env("SetLatency", "test-empty-lat"))
        resp = handle_set_latency(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.entries_updated == 0

    def test_nonexistent_pid_returns_error(self):
        req = node_agent_pb2.SetLatencyRequest(
            envelope=_env("SetLatency", "test-bad-lat"),
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    latency_ms=5.0,
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                ),
            ],
        )
        resp = handle_set_latency(req, pid_map=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.entries_updated == 0
