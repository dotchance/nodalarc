# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS input at LinkUp and inventory, owned by the creating operation.

A cross-host ISL's pod interface is created at LinkUp: the handler configures
what the creator created, verifies what it reused, refuses a mismatch, and
every LinkUp entry of a node that requires MPLS carries the read-back proof.
Nodes without the requirement are untouched. Handles carry the requirement
from the manifest; the request carries none.
"""

from __future__ import annotations

import pytest
from nodalarc.proto import node_agent_pb2
from node_agent import (
    ground_bridge,
    handlers,
    kernel_verifier,
    namespace_ops,
    substrate_monitor,
    vxlan,
)
from node_agent.command_contract import RuntimeFence
from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_kernel_inventory,
)
from node_agent.kernel_verifier import KernelStateConflict, Proof
from node_agent.mpls import MplsInputError
from node_agent.pid_discovery import NamespaceHandle

pytestmark = pytest.mark.usefixtures("_node_agent_ops_spool_path")


FENCE = RuntimeFence(session_id="demo", wiring_generation="sha256:" + "a" * 64)
SAT = "sat-P00S00"
GS = "gs-den"


@pytest.fixture(autouse=True)
def _handles_verify_live(monkeypatch):
    monkeypatch.setattr("node_agent.handlers.verify_handle", lambda handle: True)


def _env(kind: str, op: str) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id=op,
        session_id=FENCE.session_id,
        wiring_generation=FENCE.wiring_generation,
        operation_kind=kind,
    )


def _handles(pids: dict[str, int], *, mpls: frozenset[str] = frozenset()) -> dict:
    return {
        node_id: NamespaceHandle(
            node_id=node_id,
            pod_uid=f"pod-{node_id}",
            sandbox_id=f"sb-{node_id}",
            sandbox_attempt=0,
            pid=pid,
            netns_id=f"40265321{pid % 100:02d}",
            mpls_enable=node_id in mpls,
        )
        for node_id, pid in pids.items()
    }


def _cross_isl_up(op: str = "up") -> node_agent_pb2.BatchLinkUpRequest:
    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_env("BatchLinkUp", op),
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id=SAT,
                interface_name="isl0",
                link_type=node_agent_pb2.LINK_TYPE_ISL,
                locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                latency_ms=4.5,
                bandwidth_mbps=100.0,
                peer_node_id="sat-P01S00",
                peer_interface_name="isl1",
                remote_node_ip="10.0.0.2",
                vni=1001,
            )
        ],
    )


def _cross_isl_down(op: str = "down") -> node_agent_pb2.BatchLinkDownRequest:
    return node_agent_pb2.BatchLinkDownRequest(
        envelope=_env("BatchLinkDown", op),
        interfaces=[
            node_agent_pb2.InterfaceDown(
                node_id=SAT,
                interface_name="isl0",
                link_type=node_agent_pb2.LINK_TYPE_ISL,
                locality=node_agent_pb2.LOCALITY_CROSS_NODE,
                peer_node_id="sat-P01S00",
                peer_interface_name="isl1",
                remote_node_ip="10.0.0.2",
                vni=1001,
            )
        ],
    )


def _local_isl_up() -> node_agent_pb2.BatchLinkUpRequest:
    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_env("BatchLinkUp", "local-up"),
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id=SAT,
                interface_name="isl0",
                link_type=node_agent_pb2.LINK_TYPE_ISL,
                locality=node_agent_pb2.LOCALITY_LOCAL,
                latency_ms=4.5,
                bandwidth_mbps=100.0,
                peer_node_id="sat-P00S01",
                peer_interface_name="isl1",
            )
        ],
    )


def _local_ground_up() -> node_agent_pb2.BatchLinkUpRequest:
    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_env("BatchLinkUp", "ground-up"),
        interfaces=[
            node_agent_pb2.InterfaceUp(
                node_id=GS,
                interface_name="term0",
                link_type=node_agent_pb2.LINK_TYPE_GROUND,
                locality=node_agent_pb2.LOCALITY_LOCAL,
                latency_ms=4.5,
                bandwidth_mbps=100.0,
                gs_id=GS,
                sat_id=SAT,
                peer_node_id=SAT,
                peer_interface_name="gnd0",
            )
        ],
    )


class _Kernel:
    """Every kernel-touching call of the LinkUp, LinkDown and inventory paths
    replaced by recorders; proofs verified unless a test says otherwise."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        created: bool = True,
        configure_failure: Exception | None = None,
        mpls_read: Proof | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        calls = self.calls
        monkeypatch.setenv("HOST_IP", "10.0.0.1")
        monkeypatch.setattr(handlers, "_local_ip", None)
        monkeypatch.setattr(
            substrate_monitor, "require_fresh_measurement_for_remote_ip", lambda remote_ip: None
        )
        monkeypatch.setattr(substrate_monitor, "add_peer_ref", lambda ref: None)
        monkeypatch.setattr(substrate_monitor, "remove_peer_ref", lambda ref: None)

        def create(**kwargs):
            calls.append(("create_vxlan_link", kwargs["pid"], kwargs["ifname"]))
            return created

        def destroy(pid, ifname, vni):
            calls.append(("destroy_vxlan_link", pid, ifname, vni))

        def configure(pid, ifname, *, created, subject):
            calls.append(("configure_mpls_input", pid, ifname, created, subject))
            if configure_failure is not None:
                raise configure_failure
            return Proof.ok(f"mpls input enabled on {ifname}", f"device={ifname}", "observed=1")

        def read(pid, ifname):
            calls.append(("verify_mpls_input", pid, ifname))
            if mpls_read is not None:
                return mpls_read
            return Proof.ok(
                f"mpls input enabled on {ifname}",
                f"device={ifname}",
                f"key=net.mpls.conf.{ifname}.input",
                "observed=1",
            )

        def shape(pid, ifname, latency, bandwidth):
            calls.append(("apply_link_shaping", pid, ifname))

        ok = kernel_verifier.Proof.ok
        monkeypatch.setattr(vxlan, "create_vxlan_link", create)
        monkeypatch.setattr(vxlan, "destroy_vxlan_link", destroy)
        monkeypatch.setattr(handlers, "configure_mpls_input", configure)
        monkeypatch.setattr(kernel_verifier, "verify_mpls_input", read)
        monkeypatch.setattr(namespace_ops, "apply_link_shaping", shape)
        monkeypatch.setattr(
            ground_bridge, "attach_isl", lambda *a, **k: calls.append(("attach_isl",))
        )
        monkeypatch.setattr(
            ground_bridge,
            "attach_to_ground_bridge",
            lambda *a, **k: calls.append(("attach_to_ground_bridge",)),
        )
        monkeypatch.setattr(
            kernel_verifier, "verify_vxlan", lambda vni, *, local_ip, remote_ip: ok("vxlan")
        )
        monkeypatch.setattr(kernel_verifier, "verify_vxlan_absent", lambda vni: ok("vxlan absent"))
        monkeypatch.setattr(
            kernel_verifier, "verify_pod_interface_exists", lambda pid, ifname: ok(f"pod {ifname}")
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_qdisc",
            lambda pid, ifname, *, delay_ms, rate_mbps=None: ok("qdisc"),
        )
        monkeypatch.setattr(
            kernel_verifier,
            "verify_host_interface_state",
            lambda ifname, *, admin_up=None: ok(f"host {ifname}"),
        )
        monkeypatch.setattr(kernel_verifier, "verify_mirred", lambda src, dst: ok("mirred"))

    def of(self, kind: str) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == kind]


def test_cross_host_isl_creation_configures_then_the_reply_carries_the_read_back(monkeypatch):
    kernel = _Kernel(monkeypatch, created=True)

    resp = handle_batch_link_up(
        _cross_isl_up(), handles=_handles({SAT: 1234}, mpls={SAT}), fence=FENCE
    )

    assert resp.success is True
    result = resp.interface_results[0]
    assert result.success and result.verified
    assert "device=isl0" in result.proof_evidence
    assert "key=net.mpls.conf.isl0.input" in result.proof_evidence
    assert kernel.of("configure_mpls_input") == [(1234, "isl0", True, f"VNI 1001 {SAT}/isl0")]
    kinds = [c[0] for c in kernel.calls]
    assert kinds.index("create_vxlan_link") < kinds.index("configure_mpls_input")
    assert kinds.index("configure_mpls_input") < kinds.index("apply_link_shaping")
    assert kernel.of("verify_mpls_input") == [(1234, "isl0")]


def test_cross_host_isl_reuse_passes_the_creators_decision(monkeypatch):
    kernel = _Kernel(monkeypatch, created=False)

    resp = handle_batch_link_up(
        _cross_isl_up(), handles=_handles({SAT: 1234}, mpls={SAT}), fence=FENCE
    )

    assert resp.success is True
    assert kernel.of("configure_mpls_input") == [(1234, "isl0", False, f"VNI 1001 {SAT}/isl0")]


def test_recreation_after_link_down_configures_again(monkeypatch):
    """A cross-host LinkDown destroys the pod interface; the next LinkUp recreates
    it and the MPLS step runs again with nothing remembered from the first."""
    kernel = _Kernel(monkeypatch, created=True)
    handles = _handles({SAT: 1234}, mpls={SAT})

    assert handle_batch_link_up(_cross_isl_up("up-1"), handles=handles, fence=FENCE).success
    down = handle_batch_link_down(_cross_isl_down(), handles=handles, fence=FENCE)
    assert down.success, down.error_message
    assert handle_batch_link_up(_cross_isl_up("up-2"), handles=handles, fence=FENCE).success

    assert kernel.of("destroy_vxlan_link") == [(1234, "isl0", 1001)]
    assert kernel.of("configure_mpls_input") == [
        (1234, "isl0", True, f"VNI 1001 {SAT}/isl0"),
        (1234, "isl0", True, f"VNI 1001 {SAT}/isl0"),
    ]


@pytest.mark.parametrize(
    "failure",
    [
        MplsInputError(f"VNI 1001 {SAT}/isl0", "isl0", "write failed: Operation not permitted"),
        KernelStateConflict(
            f"VNI 1001 {SAT}/isl0",
            ("isl0@pod",),
            ("mpls input disabled on isl0",),
            (("device=isl0", "expected=1", "observed=0"),),
        ),
    ],
    ids=["write-failed", "reused-mismatch-refused"],
)
def test_failed_configuration_fails_the_entry_and_skips_the_carrier_stage(monkeypatch, failure):
    kernel = _Kernel(monkeypatch, created=True, configure_failure=failure)

    resp = handle_batch_link_up(
        _cross_isl_up(), handles=_handles({SAT: 1234}, mpls={SAT}), fence=FENCE
    )

    assert resp.success is False
    result = resp.interface_results[0]
    assert result.success is False
    assert result.error_code == node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED
    assert result.dirty_kernel is True
    assert str(failure) in result.error_message
    assert kernel.of("apply_link_shaping") == []
    assert kernel.of("verify_mpls_input") == []


def test_read_back_of_zero_at_the_carrier_stage_is_an_unverified_entry(monkeypatch):
    zero = Proof.fail("mpls input disabled on isl0", "device=isl0", "expected=1", "observed=0")
    _Kernel(monkeypatch, created=True, mpls_read=zero)

    resp = handle_batch_link_up(
        _cross_isl_up(), handles=_handles({SAT: 1234}, mpls={SAT}), fence=FENCE
    )

    result = resp.interface_results[0]
    assert result.success is False
    assert result.verified is False
    assert result.error_code == node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED
    assert "mpls input disabled on isl0" in result.error_message
    assert "observed=0" in result.proof_evidence


def test_node_without_the_requirement_is_untouched(monkeypatch):
    kernel = _Kernel(monkeypatch, created=True)

    resp = handle_batch_link_up(_cross_isl_up(), handles=_handles({SAT: 1234}), fence=FENCE)

    assert resp.success is True
    assert kernel.of("configure_mpls_input") == []
    assert kernel.of("verify_mpls_input") == []
    assert "device=isl0" not in resp.interface_results[0].proof_evidence


def test_local_isl_is_verified_only(monkeypatch):
    """The local ISL's pod interface came from wiring; LinkUp proves its
    configuration and configures nothing."""
    kernel = _Kernel(monkeypatch)

    resp = handle_batch_link_up(
        _local_isl_up(), handles=_handles({SAT: 1234, "sat-P00S01": 1235}, mpls={SAT}), fence=FENCE
    )

    assert resp.success is True
    assert kernel.of("configure_mpls_input") == []
    assert kernel.of("verify_mpls_input") == [(1234, "isl0")]
    assert "key=net.mpls.conf.isl0.input" in resp.interface_results[0].proof_evidence


def test_local_ground_checks_each_endpoint_by_its_own_requirement(monkeypatch):
    kernel = _Kernel(monkeypatch)

    resp = handle_batch_link_up(
        _local_ground_up(), handles=_handles({GS: 2222, SAT: 1234}, mpls={GS}), fence=FENCE
    )

    assert resp.success is True
    assert kernel.of("verify_mpls_input") == [(2222, "term0")]

    kernel = _Kernel(monkeypatch)
    resp = handle_batch_link_up(
        _local_ground_up(), handles=_handles({GS: 2222, SAT: 1234}, mpls={GS, SAT}), fence=FENCE
    )
    assert resp.success is True
    assert sorted(kernel.of("verify_mpls_input")) == [(1234, "gnd0"), (2222, "term0")]


def test_inventory_carries_the_proof_for_required_endpoints_in_both_expected_states(monkeypatch):
    kernel = _Kernel(monkeypatch)
    local = node_agent_pb2.KernelInventoryEntry(
        node_id=GS,
        interface_name="term0",
        link_type=node_agent_pb2.LINK_TYPE_GROUND,
        locality=node_agent_pb2.LOCALITY_LOCAL,
        gs_id=GS,
        sat_id=SAT,
        peer_node_id=SAT,
        peer_interface_name="gnd0",
        latency_ms=0.0,
        bandwidth_mbps=0.0,
        expected_admin_up=False,
    )
    cross = node_agent_pb2.KernelInventoryEntry(
        node_id=SAT,
        interface_name="gnd0",
        link_type=node_agent_pb2.LINK_TYPE_GROUND,
        locality=node_agent_pb2.LOCALITY_CROSS_NODE,
        gs_id=GS,
        sat_id=SAT,
        peer_node_id=GS,
        peer_interface_name="term0",
        remote_node_ip="10.0.0.2",
        vni=1001,
        latency_ms=4.5,
        bandwidth_mbps=100.0,
        expected_admin_up=True,
    )
    req = node_agent_pb2.KernelInventoryRequest(
        envelope=_env("KernelInventory", "inv"), gs_id=GS, entries=[local, cross]
    )

    resp = handle_kernel_inventory(
        req, handles=_handles({GS: 2222, SAT: 1234}, mpls={SAT}), fence=FENCE
    )

    assert resp.success is True, resp.error_message
    assert sorted(kernel.of("verify_mpls_input")) == [(1234, "gnd0"), (1234, "gnd0")]
    assert all(entry.verified for entry in resp.entry_results)
