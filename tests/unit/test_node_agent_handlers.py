"""Node Agent handler tests — call handlers directly, no transport.

Tests handler logic:
- Per-interface locality (LOCAL/CROSS_NODE)
- Empty batches succeed
- Bad PIDs return structured errors
- None pid_map raises ValueError (wiring never happened)
"""

from __future__ import annotations

import logging

import pytest
from nodalarc.proto import node_agent_pb2
from node_agent import ops_events
from node_agent.command_contract import RuntimeFence
from node_agent.handlers import (
    EntryOutcome,
    _publish_command_event,
    handle_batch_link_down,
    handle_batch_link_up,
    handle_kernel_inventory,
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


class TestCommandEvents:
    def test_successful_command_applied_is_debug_only(self, caplog, monkeypatch):
        published = []
        monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

        with caplog.at_level(logging.DEBUG, logger="node_agent.handlers"):
            _publish_command_event(
                operation="BatchLinkUp",
                envelope=_env("BatchLinkUp", "test-link-up"),
                outcomes=[EntryOutcome()],
            )

        assert published == []
        records = [
            record
            for record in caplog.records
            if getattr(record, "code", None) == "COMMAND_APPLIED"
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.DEBUG
        assert records[0].details["command_type"] == "BatchLinkUp"


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
            "require_fresh_measurement_for_remote_ip",
            lambda remote_ip: calls.append(("substrate_check", remote_ip)),
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
        assert ("substrate_check", "10.0.0.2") in calls
        assert ("shape", 1234, "gnd0", 4.5, 100.0) in calls
        assert ("peer_ref", "10.0.0.2", 1001, "gnd0") in calls

    def test_cross_node_isl_requires_substrate_evidence_before_mutation(self, monkeypatch):
        from node_agent import handlers, substrate_monitor, vxlan

        calls: list[tuple] = []

        def _missing_substrate(remote_ip: str) -> None:
            raise RuntimeError("no local substrate status")

        monkeypatch.setenv("HOST_IP", "10.0.0.1")
        monkeypatch.setattr(handlers, "_local_ip", None)
        monkeypatch.setattr(
            substrate_monitor,
            "require_fresh_measurement_for_remote_ip",
            _missing_substrate,
        )
        monkeypatch.setattr(
            vxlan,
            "create_vxlan_link",
            lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        )
        monkeypatch.setattr(
            substrate_monitor,
            "add_peer_ref",
            lambda ref: calls.append(("peer_ref", ref.remote_ip, ref.vni, ref.local_ifname)),
        )

        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=_env("BatchLinkUp", "test-cross-isl-substrate-missing"),
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                    latency_ms=4.5,
                    bandwidth_mbps=100.0,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                    remote_node_ip="10.0.0.2",
                    vni=1001,
                )
            ],
        )

        resp = handle_batch_link_up(req, pid_map={"sat-P00S00": 1234}, fence=FENCE)

        assert resp.success is False
        assert resp.dirty_kernel is False
        assert calls == []
        assert resp.interface_results[0].success is False
        assert resp.interface_results[0].error_code == node_agent_pb2.NODE_AGENT_DEPENDENCY_MISSING
        assert "Substrate measurement unavailable" in resp.interface_results[0].error_message


class TestKernelInventory:
    def _request(
        self, entry: node_agent_pb2.KernelInventoryEntry
    ) -> node_agent_pb2.KernelInventoryRequest:
        return node_agent_pb2.KernelInventoryRequest(
            envelope=_env("KernelInventory", "test-kernel-inventory"),
            target_sim_time="2026-06-01T00:00:00Z",
            gs_id="gs-den",
            entries=[entry],
        )

    def _cross_node_entry(
        self, *, expected_admin_up: bool = True, remote_node_ip: str = "10.0.0.2"
    ):
        return node_agent_pb2.KernelInventoryEntry(
            node_id="sat-P00S00",
            interface_name="gnd0",
            link_type=node_agent_pb2.LINK_TYPE_GROUND,
            locality=node_agent_pb2.LOCALITY_CROSS_NODE,
            gs_id="gs-den",
            sat_id="sat-P00S00",
            peer_node_id="gs-den",
            peer_interface_name="term0",
            remote_node_ip=remote_node_ip,
            vni=1001,
            latency_ms=4.5 if expected_admin_up else 0.0,
            bandwidth_mbps=100.0 if expected_admin_up else 0.0,
            expected_admin_up=expected_admin_up,
        )

    def _local_entry(self, *, expected_admin_up: bool = True):
        return node_agent_pb2.KernelInventoryEntry(
            node_id="gs-den",
            interface_name="term0",
            link_type=node_agent_pb2.LINK_TYPE_GROUND,
            locality=node_agent_pb2.LOCALITY_LOCAL,
            gs_id="gs-den",
            sat_id="sat-P00S00",
            peer_node_id="sat-P00S00",
            peer_interface_name="gnd0",
            latency_ms=4.5 if expected_admin_up else 0.0,
            bandwidth_mbps=100.0 if expected_admin_up else 0.0,
            expected_admin_up=expected_admin_up,
        )

    def test_request_rejects_isl_entries(self, monkeypatch):
        monkeypatch.setattr(ops_events, "publish", lambda **_kwargs: None)
        entry = self._cross_node_entry()
        entry.link_type = node_agent_pb2.LINK_TYPE_ISL

        resp = handle_kernel_inventory(
            self._request(entry), pid_map={"sat-P00S00": 1234}, fence=FENCE
        )

        assert resp.success is False
        assert resp.error_code == node_agent_pb2.NODE_AGENT_INVALID_FIELD
        assert "supports ground entries only" in resp.error_message
        assert resp.dirty_kernel is False

    def test_cross_node_request_rejects_missing_vni(self, monkeypatch):
        monkeypatch.setattr(ops_events, "publish", lambda **_kwargs: None)
        entry = self._cross_node_entry()
        entry.vni = 0

        resp = handle_kernel_inventory(
            self._request(entry), pid_map={"sat-P00S00": 1234}, fence=FENCE
        )

        assert resp.success is False
        assert resp.error_code == node_agent_pb2.NODE_AGENT_INVALID_FIELD
        assert "requires vni > 0" in resp.error_message
        assert resp.dirty_kernel is False

    def test_cross_node_request_requires_remote_endpoint_identity(self, monkeypatch):
        monkeypatch.setattr(ops_events, "publish", lambda **_kwargs: None)
        req = self._request(self._cross_node_entry(remote_node_ip=""))

        resp = handle_kernel_inventory(req, pid_map={"sat-P00S00": 1234}, fence=FENCE)

        assert resp.success is False
        assert resp.error_code == node_agent_pb2.NODE_AGENT_INVALID_FIELD
        assert "remote_node_ip" in resp.error_message
        assert resp.dirty_kernel is False

    def test_kernel_inventory_handler_is_read_only(self, monkeypatch):
        from node_agent import ground_bridge, kernel_actuator, kernel_verifier, vxlan

        def _mutation_forbidden(*_args, **_kwargs):
            raise AssertionError("KernelInventory must not mutate kernel state")

        for module, names in (
            (
                ground_bridge,
                (
                    "attach_to_ground_bridge",
                    "detach_from_ground_bridge",
                    "attach_isl",
                    "detach_isl",
                    "create_ground_bridge",
                    "create_satellite_ground_veth",
                    "create_mediated_isl",
                ),
            ),
            (
                kernel_actuator,
                (
                    "create_cross_node_vxlan",
                    "destroy_cross_node_vxlan",
                    "attach_cross_node_ground",
                    "detach_cross_node_ground",
                    "detach_local_isl",
                ),
            ),
            (
                vxlan,
                (
                    "create_vxlan_link",
                    "destroy_vxlan_link",
                    "attach_cross_node_ground",
                    "detach_cross_node_ground",
                ),
            ),
        ):
            for name in names:
                monkeypatch.setattr(module, name, _mutation_forbidden)

        monkeypatch.setattr(
            kernel_verifier,
            "verify_host_interface_state",
            lambda ifname, *, admin_up: kernel_verifier.Proof.ok(f"host {ifname} {admin_up}"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_qdisc",
            lambda pid, ifname, *, delay_ms, rate_mbps=None: kernel_verifier.Proof.ok("qdisc"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_mirred",
            lambda src, dst: kernel_verifier.Proof.ok(f"mirred {src}->{dst}"),
        )

        resp = handle_kernel_inventory(
            self._request(self._local_entry()),
            pid_map={"gs-den": 2222, "sat-P00S00": 1234},
            fence=FENCE,
        )

        assert resp.success is True
        assert resp.dirty_kernel is False
        assert resp.entry_results[0].verified is True

    def test_cross_node_expected_up_verifies_exact_vxlan_endpoint(self, monkeypatch):
        from node_agent import handlers, kernel_verifier

        calls: list[tuple] = []
        monkeypatch.setenv("HOST_IP", "10.0.0.1")
        monkeypatch.setattr(handlers, "_local_ip", None)
        monkeypatch.setattr(
            kernel_verifier,
            "verify_host_interface_state",
            lambda ifname, *, admin_up: kernel_verifier.Proof.ok(f"host {ifname} {admin_up}"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_qdisc",
            lambda pid, ifname, *, delay_ms, rate_mbps=None: kernel_verifier.Proof.ok("qdisc"),
        )

        def _verify_vxlan(vni: int, *, local_ip: str, remote_ip: str):
            calls.append(("vxlan", vni, local_ip, remote_ip))
            return kernel_verifier.Proof.ok("vxlan")

        monkeypatch.setattr(kernel_verifier, "verify_vxlan", _verify_vxlan)
        monkeypatch.setattr(
            kernel_verifier,
            "verify_mirred",
            lambda src, dst: kernel_verifier.Proof.ok(f"mirred {src}->{dst}"),
        )

        resp = handle_kernel_inventory(
            self._request(self._cross_node_entry()),
            pid_map={"sat-P00S00": 1234},
            fence=FENCE,
        )

        assert resp.success is True
        assert resp.entries_verified == 1
        assert calls == [("vxlan", 1001, "10.0.0.1", "10.0.0.2")]
        assert resp.entry_results[0].verified is True

    def test_cross_node_expected_down_verifies_vxlan_absence(self, monkeypatch):
        from node_agent import kernel_verifier

        calls: list[tuple] = []
        monkeypatch.setattr(
            kernel_verifier,
            "verify_host_interface_state",
            lambda ifname, *, admin_up: kernel_verifier.Proof.ok(f"host {ifname} {admin_up}"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_pod_interface_exists",
            lambda pid, ifname: kernel_verifier.Proof.ok(f"pod {ifname}"),
        )

        def _verify_absent(vni: int):
            calls.append(("vxlan_absent", vni))
            return kernel_verifier.Proof.ok("vxlan absent")

        monkeypatch.setattr(kernel_verifier, "verify_vxlan_absent", _verify_absent)

        resp = handle_kernel_inventory(
            self._request(self._cross_node_entry(expected_admin_up=False)),
            pid_map={"sat-P00S00": 1234},
            fence=FENCE,
        )

        assert resp.success is True
        assert calls == [("vxlan_absent", 1001)]
        assert resp.entry_results[0].verified is True

    def test_cross_node_proof_exception_marks_dirty(self, monkeypatch):
        from node_agent import handlers, kernel_verifier

        monkeypatch.setenv("HOST_IP", "10.0.0.1")
        monkeypatch.setattr(handlers, "_local_ip", None)
        monkeypatch.setattr(
            kernel_verifier,
            "verify_host_interface_state",
            lambda ifname, *, admin_up: kernel_verifier.Proof.ok(f"host {ifname} {admin_up}"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_qdisc",
            lambda pid, ifname, *, delay_ms, rate_mbps=None: kernel_verifier.Proof.ok("qdisc"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_vxlan",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("wrong endpoint")),
        )

        resp = handle_kernel_inventory(
            self._request(self._cross_node_entry()),
            pid_map={"sat-P00S00": 1234},
            fence=FENCE,
        )

        assert resp.success is False
        assert resp.dirty_kernel is True
        assert resp.error_code == node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED
        assert resp.entry_results[0].dirty_kernel is True
        assert "wrong endpoint" in resp.entry_results[0].error_message


class TestSetLatency:
    def test_empty_request_succeeds_without_operator_ops_event(self, monkeypatch):
        published = []
        monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

        req = node_agent_pb2.SetLatencyRequest(envelope=_env("SetLatency", "test-empty-lat"))
        resp = handle_set_latency(req, pid_map=EMPTY_PID_MAP, fence=FENCE)

        assert resp.success is True
        assert resp.entries_updated == 0
        assert published == []

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

    def test_failed_request_still_publishes_operator_ops_event(self, monkeypatch):
        published = []
        monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

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
        assert len(published) == 1
        assert published[0]["level"] in {"warning", "critical"}
        assert published[0]["code"] in {"COMMAND_FAILED", "DIRTY_KERNEL"}
        assert published[0]["details"]["command_type"] == "SetLatency"
