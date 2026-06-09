# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Node Agent NATS command handler implementations.

Executes kernel operations dispatched by scheduler/dispatcher.py via
NATS request/reply. Uses namespace_ops.py and ground_bridge.py for
all netlink operations (setns-based, no fork).

IMPORTANT — node ID contract:
  Node IDs in Node Agent protobuf messages MUST use the runtime node ID from
  the resolved session manifest, not the sanitized K8s pod name. The ground
  bridge naming helpers derive host veth names from that node ID, and Linux
  interface names are case-sensitive.

Error handling: every per-link operation is wrapped in try/except.
A single failing link does not prevent other links in the batch from
being processed. Failures are logged with full context and returned
in the protobuf response error field.
"""

from __future__ import annotations

import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from nodalarc.proto import node_agent_pb2

from node_agent import (
    ground_bridge,
    kernel_actuator,
    kernel_verifier,
    namespace_ops,
    ops_events,
    vxlan,
)
from node_agent.command_contract import (
    CommandContractError,
    RuntimeFence,
    validate_batch_link_down_request,
    validate_batch_link_up_request,
    validate_kernel_inventory_request,
    validate_set_latency_request,
    worst_error_code,
)
from node_agent.operation_executor import execute_plan
from node_agent.operation_plan import OperationPlan, OperationStep

log = logging.getLogger(__name__)

# Thread pool for concurrent batch execution within a single NATS command.
# Bounded to avoid resource exhaustion on large batches.
_BATCH_POOL = ThreadPoolExecutor(max_workers=8)


# ---------------------------------------------------------------------------
# PID resolution — Node Agent owns all PIDs, Scheduler never supplies them
# ---------------------------------------------------------------------------


_local_ip: str | None = None


def _discover_local_ip() -> str:
    """Discover this node's InternalIP. Cached after first call.

    Uses HOST_IP env var injected by K8s downward API in the DaemonSet spec.
    This is the node's real IP, not the pod's CNI IP.
    """
    global _local_ip
    if _local_ip is not None:
        return _local_ip
    import os

    _local_ip = os.environ.get("HOST_IP", "")
    if not _local_ip:
        raise RuntimeError("HOST_IP env var is required for VXLAN-capable Node Agent")
    return _local_ip


class PidNotFoundError(Exception):
    """Raised when a node_id has no PID in the Node Agent's pid_map."""


def _require_pid(node_id: str, pid_map: dict[str, int]) -> int:
    """Look up PID for node_id. Raises PidNotFoundError if missing."""
    pid = pid_map.get(node_id, 0)
    if pid == 0:
        raise PidNotFoundError(
            f"node_id '{node_id}' not in pid_map — pod not wired or not on this node"
        )
    return pid


def _extract_ground_ifaces(iface) -> tuple[str, str]:
    """Extract (gs_ifname, sat_ifname) from a ground link protobuf.

    The Scheduler sets interface_name for node_id's side and
    peer_interface_name for the other side. Both must be present.
    """
    if not iface.peer_interface_name:
        raise ValueError(
            f"Ground link {iface.gs_id}<->{iface.sat_id}: "
            "peer_interface_name not set — "
            "Scheduler must populate both interface names"
        )
    if iface.node_id == iface.gs_id:
        return iface.interface_name, iface.peer_interface_name
    return iface.peer_interface_name, iface.interface_name


def _iface_key(iface) -> tuple[str, str]:
    return (iface.node_id, iface.interface_name)


@dataclass(frozen=True)
class EntryOutcome:
    error_code: int = node_agent_pb2.NODE_AGENT_OK
    error_message: str = ""
    verified: bool = True
    dirty_kernel: bool = False
    proof_summary: str = "operation verified"
    proof_evidence: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return (
            self.error_code == node_agent_pb2.NODE_AGENT_OK
            and not self.error_message
            and self.verified
            and not self.dirty_kernel
        )


def _ok(proof: kernel_verifier.Proof | None = None) -> EntryOutcome:
    if proof is None:
        return EntryOutcome()
    return EntryOutcome(
        verified=proof.verified,
        error_code=(
            node_agent_pb2.NODE_AGENT_OK
            if proof.verified
            else node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED
        ),
        error_message="" if proof.verified else proof.summary,
        dirty_kernel=not proof.verified,
        proof_summary=proof.summary,
        proof_evidence=proof.evidence,
    )


def _fail(
    code: int,
    message: str,
    *,
    verified: bool = False,
    dirty_kernel: bool = False,
    proof: kernel_verifier.Proof | None = None,
) -> EntryOutcome:
    return EntryOutcome(
        error_code=code,
        error_message=message,
        verified=verified,
        dirty_kernel=dirty_kernel,
        proof_summary=proof.summary if proof else message,
        proof_evidence=proof.evidence if proof else (),
    )


def _combine_proofs(summary: str, proofs: list[kernel_verifier.Proof]) -> EntryOutcome:
    failures = [proof for proof in proofs if not proof.verified]
    evidence = tuple(item for proof in proofs for item in proof.evidence)
    if failures:
        return _fail(
            node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
            "; ".join(proof.summary for proof in failures),
            dirty_kernel=True,
            proof=kernel_verifier.Proof.fail(summary, *evidence),
        )
    return EntryOutcome(
        proof_summary=summary,
        proof_evidence=evidence,
    )


def _interface_result(iface, outcome: EntryOutcome) -> node_agent_pb2.InterfaceResult:
    return node_agent_pb2.InterfaceResult(
        node_id=iface.node_id,
        interface_name=iface.interface_name,
        success=outcome.success,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        verified=outcome.verified,
        dirty_kernel=outcome.dirty_kernel,
        proof_summary=outcome.proof_summary,
        proof_evidence=list(outcome.proof_evidence),
    )


def _latency_result(entry, outcome: EntryOutcome) -> node_agent_pb2.LatencyResult:
    return node_agent_pb2.LatencyResult(
        node_id=entry.node_id,
        interface_name=entry.interface_name,
        success=outcome.success,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        verified=outcome.verified,
        dirty_kernel=outcome.dirty_kernel,
        proof_summary=outcome.proof_summary,
        proof_evidence=list(outcome.proof_evidence),
    )


def _aggregate_response_code(outcomes: list[EntryOutcome]) -> int:
    return worst_error_code(outcome.error_code for outcome in outcomes)


def _dirty(outcomes: list[EntryOutcome]) -> bool:
    return any(outcome.dirty_kernel for outcome in outcomes)


def _error_code_name(code: int) -> str:
    try:
        return node_agent_pb2.NodeAgentErrorCode.Name(code)
    except ValueError:
        return str(code)


def _publish_command_event(
    *,
    operation: str,
    envelope: node_agent_pb2.CommandEnvelope,
    outcomes: list[EntryOutcome],
) -> None:
    failed = [outcome for outcome in outcomes if not outcome.success]
    if not failed:
        log.debug(
            "%s applied [operation_id=%s, entries=%d]",
            operation,
            envelope.operation_id,
            len(outcomes),
            extra={
                "code": "COMMAND_APPLIED",
                "details": {
                    "operation_id": envelope.operation_id,
                    "wiring_generation": envelope.wiring_generation,
                    "command_type": operation,
                    "entry_count": len(outcomes),
                },
            },
        )
        return
    dirty = _dirty(outcomes)
    code = _error_code_name(_aggregate_response_code(failed))
    ops_events.publish(
        level="critical" if dirty else "error",
        code="DIRTY_KERNEL" if dirty else "COMMAND_FAILED",
        message=f"{operation} failed",
        session_id=envelope.session_id,
        details={
            "operation_id": envelope.operation_id,
            "wiring_generation": envelope.wiring_generation,
            "command_type": operation,
            "error_code": code,
            "dirty_kernel": dirty,
            "failed_count": len(failed),
            "entry_count": len(outcomes),
            "failures": [
                {
                    "error_code": _error_code_name(outcome.error_code),
                    "error_message": outcome.error_message,
                    "dirty_kernel": outcome.dirty_kernel,
                    "verified": outcome.verified,
                    "proof_summary": outcome.proof_summary,
                }
                for outcome in failed[:20]
            ],
        },
    )


def _batch_down_failure(
    request: node_agent_pb2.BatchLinkDownRequest,
    *,
    code: int,
    message: str,
    dirty_kernel: bool = False,
) -> node_agent_pb2.BatchLinkDownResponse:
    outcome = _fail(code, message, dirty_kernel=dirty_kernel)
    return node_agent_pb2.BatchLinkDownResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
        interfaces_downed=0,
        apply_time_ms=0,
        interface_results=[_interface_result(iface, outcome) for iface in request.interfaces],
    )


def _batch_up_failure(
    request: node_agent_pb2.BatchLinkUpRequest,
    *,
    code: int,
    message: str,
    dirty_kernel: bool = False,
) -> node_agent_pb2.BatchLinkUpResponse:
    outcome = _fail(code, message, dirty_kernel=dirty_kernel)
    return node_agent_pb2.BatchLinkUpResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
        interfaces_upped=0,
        apply_time_ms=0,
        interface_results=[_interface_result(iface, outcome) for iface in request.interfaces],
    )


def _set_latency_failure(
    request: node_agent_pb2.SetLatencyRequest,
    *,
    code: int,
    message: str,
    dirty_kernel: bool = False,
) -> node_agent_pb2.SetLatencyResponse:
    outcome = _fail(code, message, dirty_kernel=dirty_kernel)
    return node_agent_pb2.SetLatencyResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
        entries_updated=0,
        entry_results=[_latency_result(entry, outcome) for entry in request.entries],
    )


# ---------------------------------------------------------------------------
# Per-link operation functions (called concurrently within batches)
# ---------------------------------------------------------------------------


def _isl_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> EntryOutcome:
    """Deactivate an ISL link.

    LOCAL ISLs: detaches host-side veths (brings host-side DOWN → carrier
    drops on pod-side). Pod-side interface remains admin UP in LOWERLAYERDOWN
    state. Pod-side tc qdiscs are left in place — apply_link_shaping uses
    replace semantics on the next LinkUp, so orphaned qdiscs are harmless.

    CROSS_NODE ISLs: destroys the VXLAN tunnel (handled separately in the
    batch handler's pre-mutation teardown).
    """
    try:
        if iface.locality == node_agent_pb2.LOCALITY_LOCAL:
            ground_bridge.detach_isl(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
            )
            host_a = ground_bridge._isl_host_name(
                iface.node_id, ground_bridge._isl_idx_from_ifname(iface.interface_name)
            )
            host_b = ground_bridge._isl_host_name(
                iface.peer_node_id, ground_bridge._isl_idx_from_ifname(iface.peer_interface_name)
            )
            return _combine_proofs(
                "LOCAL ISL LinkDown verified",
                [
                    kernel_verifier.verify_host_interface_state(host_a, admin_up=False),
                    kernel_verifier.verify_host_interface_state(host_b, admin_up=False),
                ],
            )
        if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE and iface.vni:
            return _ok(kernel_verifier.verify_vxlan_absent(iface.vni))
        return _ok()
    except Exception as exc:
        msg = f"ISL down failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _ground_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> EntryOutcome:
    """Tear down a ground link.

    Ground LinkDown sequence:
    1. Remove tc mirred redirect from host-side veths
    2. Bring satellite host-side veth DOWN — carrier drops on GS gnd0
       automatically (UP → LOWERLAYERDOWN), FRR tears down adjacency
    3. Bring satellite gnd0 DOWN

    Pod-side tc qdiscs on both gnd0 interfaces are left in place —
    apply_link_shaping uses replace semantics on the next LinkUp.

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pm = pid_map
        sat_pid = _require_pid(iface.sat_id, pm)
        gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
        ground_bridge.detach_from_ground_bridge(
            iface.gs_id,
            iface.sat_id,
            sat_pid,
            gs_ifname=gs_ifname,
            sat_ifname=sat_ifname,
        )
        gs_port = ground_bridge._gs_host_veth(iface.gs_id, gs_ifname)
        sat_host = ground_bridge._sat_host_veth(iface.sat_id, sat_ifname)
        return _combine_proofs(
            "LOCAL ground LinkDown verified",
            [
                kernel_verifier.verify_host_interface_state(gs_port, admin_up=False),
                kernel_verifier.verify_host_interface_state(sat_host, admin_up=False),
                kernel_verifier.verify_pod_interface_exists(sat_pid, sat_ifname),
            ],
        )
    except Exception as exc:
        msg = f"Ground down failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _ground_link_up(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> EntryOutcome:
    """Bring up a ground link with bridge attach + shaping.

    Ground LinkUp sequence:
    1. attach_to_ground_bridge (host veths UP, sat gnd0 UP, mirred redirect)
       — carrier arrives on GS gnd0 automatically (LOWERLAYERDOWN → UP)
    2. Apply tc shaping on GS gnd0
    3. Apply tc shaping on satellite gnd0

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.

    Layer 3 neighbor resolution (NDP) is NOT performed here. NDP is owned
    by FRR (IGP sessions, via IS-IS/OSPF hello) and the kernel NDP state
    machine (NodalPath sessions, on first forwarding attempt). See PRD
    §13.22.6 — v0.72 reversal.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pm = pid_map
        sat_pid = _require_pid(iface.sat_id, pm)
        gs_pid = _require_pid(iface.gs_id, pm)
        gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
        ground_bridge.attach_to_ground_bridge(
            iface.gs_id,
            iface.sat_id,
            sat_pid,
            gs_ifname=gs_ifname,
            sat_ifname=sat_ifname,
        )
        namespace_ops.apply_link_shaping(gs_pid, gs_ifname, iface.latency_ms, iface.bandwidth_mbps)
        namespace_ops.apply_link_shaping(
            sat_pid, sat_ifname, iface.latency_ms, iface.bandwidth_mbps
        )
        gs_port = ground_bridge._gs_host_veth(iface.gs_id, gs_ifname)
        sat_host = ground_bridge._sat_host_veth(iface.sat_id, sat_ifname)
        return _combine_proofs(
            "LOCAL ground LinkUp verified",
            [
                kernel_verifier.verify_host_interface_state(gs_port, admin_up=True),
                kernel_verifier.verify_host_interface_state(sat_host, admin_up=True),
                kernel_verifier.verify_mirred(gs_port, sat_host),
                kernel_verifier.verify_mirred(sat_host, gs_port),
                kernel_verifier.verify_qdisc(
                    gs_pid, gs_ifname, delay_ms=iface.latency_ms, rate_mbps=iface.bandwidth_mbps
                ),
                kernel_verifier.verify_qdisc(
                    sat_pid, sat_ifname, delay_ms=iface.latency_ms, rate_mbps=iface.bandwidth_mbps
                ),
            ],
        )
    except Exception as exc:
        msg = f"Ground up failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _update_latency_entry(
    entry: node_agent_pb2.LatencyEntry, pid_map: dict[str, int] | None = None
) -> EntryOutcome:
    """Update netem delay on a single interface.

    Uses tc "change" — does NOT touch admin state or re-add qdiscs.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pm = pid_map
        pid = _require_pid(entry.node_id, pm)
        plan = OperationPlan(
            operation_id=f"SetLatency:{entry.node_id}:{entry.interface_name}",
            operation_kind="SetLatency",
            target=f"{entry.node_id}/{entry.interface_name}",
            steps=(
                OperationStep(
                    name="tc-netem-change",
                    action=lambda: kernel_actuator.update_terminal_delay(
                        pid,
                        entry.interface_name,
                        entry.latency_ms,
                    ),
                    verify=lambda: kernel_verifier.verify_qdisc(
                        pid,
                        entry.interface_name,
                        delay_ms=entry.latency_ms,
                    ),
                    dirty_on_failure=True,
                ),
            ),
        )
        result = execute_plan(plan)
        if not result.success:
            return _fail(
                node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
                result.error_message,
                dirty_kernel=result.dirty_kernel,
            )
        return _combine_proofs("SetLatency verified", result.proofs)
    except Exception as exc:
        msg = f"Latency update failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


# ---------------------------------------------------------------------------
# NATS command handler functions (called from server.py)
# ---------------------------------------------------------------------------


def handle_batch_link_down(
    request: node_agent_pb2.BatchLinkDownRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.BatchLinkDownResponse:
    """Handle BatchLinkDown — per-interface locality."""
    start = _time.monotonic()
    outcomes: dict[tuple[str, str], EntryOutcome] = {}
    downed = 0

    if fence is None:
        raise ValueError("RuntimeFence is required")
    try:
        validate_batch_link_down_request(request, fence=fence)
    except CommandContractError as exc:
        ops_events.publish(
            level="warning",
            code="COMMAND_REJECTED",
            message=f"BatchLinkDown rejected: {exc.message}",
            session_id=fence.session_id,
            details={
                "operation_id": request.envelope.operation_id,
                "wiring_generation": fence.wiring_generation,
                "command_type": "BatchLinkDown",
                "error_code": _error_code_name(exc.code),
            },
        )
        return _batch_down_failure(request, code=exc.code, message=exc.message)

    # Submit all operations concurrently — each interface carries its own locality
    futures = {}
    if pid_map is None:
        raise ValueError("pid_map is None — wiring not complete?")
    pm = pid_map
    for iface in request.interfaces:
        if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE and iface.vni:
            if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
                # CROSS_NODE GROUND: detach VXLAN from existing host-side interface
                gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
                is_sat = iface.node_id == iface.sat_id
                if is_sat:
                    host_ifname = ground_bridge._sat_host_veth(iface.node_id, sat_ifname)
                    sat_pid = pm.get(iface.node_id, 0)
                else:
                    host_ifname = ground_bridge._gs_host_veth(iface.node_id, gs_ifname)
                    sat_pid = None
                fut = _BATCH_POOL.submit(
                    vxlan.detach_cross_node_ground,
                    host_ifname,
                    iface.vni,
                    sat_pid if is_sat else None,
                    sat_ifname,
                )
            else:
                # CROSS_NODE ISL: destroy full VXLAN + veth pair
                try:
                    pid = _require_pid(iface.node_id, pm)
                except Exception as exc:
                    outcomes[_iface_key(iface)] = _fail(
                        node_agent_pb2.NODE_AGENT_PID_NOT_FOUND, str(exc)
                    )
                    continue
                fut = _BATCH_POOL.submit(
                    vxlan.destroy_vxlan_link, pid, iface.interface_name, iface.vni
                )
        elif iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
            fut = _BATCH_POOL.submit(_ground_link_down, iface, pm)
        else:
            fut = _BATCH_POOL.submit(_isl_link_down, iface, pm)
        futures[fut] = iface

    # Collect results — wait for ALL before returning ACK
    from node_agent import substrate_monitor

    for fut in as_completed(futures):
        iface = futures[fut]
        try:
            raw_result = fut.result(timeout=10)
            if isinstance(raw_result, EntryOutcome):
                outcome = raw_result
            else:
                proof = (
                    kernel_verifier.verify_vxlan_absent(iface.vni)
                    if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE and iface.vni
                    else kernel_verifier.Proof.ok("cleanup operation completed")
                )
                outcome = _ok(proof)
            if outcome.success:
                downed += 1
                # Track exact VXLAN peer lifecycle for diagnostics.
                if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE and iface.remote_node_ip:
                    substrate_monitor.remove_peer_ref(
                        substrate_monitor.PeerRef(
                            session_id=fence.session_id,
                            wiring_generation=fence.wiring_generation,
                            remote_ip=iface.remote_node_ip,
                            vni=iface.vni,
                            local_ifname=iface.interface_name,
                        )
                    )
            outcomes[_iface_key(iface)] = outcome
        except Exception as exc:
            msg = f"Unexpected error for {iface.node_id}/{iface.interface_name}: {exc}"
            outcomes[_iface_key(iface)] = _fail(
                node_agent_pb2.NODE_AGENT_CLEANUP_FAILED,
                msg,
                dirty_kernel=True,
            )

    elapsed = (_time.monotonic() - start) * 1000
    ordered_outcomes = [
        outcomes.get(
            _iface_key(iface),
            _fail(node_agent_pb2.NODE_AGENT_INTERNAL_ERROR, "not attempted"),
        )
        for iface in request.interfaces
    ]
    errors = [outcome.error_message for outcome in ordered_outcomes if not outcome.success]
    error_msg = "; ".join(errors)
    success = all(outcome.success for outcome in ordered_outcomes)
    if not success:
        ifaces = ", ".join(f"{i.node_id}/{i.interface_name}" for i in request.interfaces)
        log.warning(
            "BatchLinkDown: %d/%d downed (%.1fms) [%s]: %s",
            downed,
            len(request.interfaces),
            elapsed,
            ifaces,
            error_msg,
        )
    else:
        ifaces = ", ".join(f"{i.node_id}/{i.interface_name}" for i in request.interfaces)
        log.debug("BatchLinkDown: %d downed (%.1fms) [%s]", downed, elapsed, ifaces)

    _publish_command_event(
        operation="BatchLinkDown",
        envelope=request.envelope,
        outcomes=ordered_outcomes,
    )
    return node_agent_pb2.BatchLinkDownResponse(
        success=success,
        error_code=_aggregate_response_code(ordered_outcomes),
        error_message=error_msg,
        dirty_kernel=_dirty(ordered_outcomes),
        interfaces_downed=downed,
        apply_time_ms=elapsed,
        interface_results=[
            _interface_result(iface, outcome)
            for iface, outcome in zip(request.interfaces, ordered_outcomes, strict=True)
        ],
    )


def _isl_link_up_carrier_stage(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> EntryOutcome:
    """ISL link-up carrier stage: attach host-side + shaping. No NDP yet.

    LOCAL ISLs: pod-side interface is already admin UP (from wiring). Attaches
    host-side veths (UP + tc mirred) → carrier appears on pod-side.

    CROSS_NODE ISLs: VXLAN tunnel + veth already created in the preparation stage and
    interface is UP. Only tc shaping needs to be applied.

    Returns error string or None.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pid = _require_pid(iface.node_id, pid_map)
        if iface.locality == node_agent_pb2.LOCALITY_LOCAL:
            ground_bridge.attach_isl(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
            )
        namespace_ops.apply_link_shaping(
            pid, iface.interface_name, iface.latency_ms, iface.bandwidth_mbps
        )
        proofs = [
            kernel_verifier.verify_pod_interface_exists(pid, iface.interface_name),
            kernel_verifier.verify_qdisc(
                pid,
                iface.interface_name,
                delay_ms=iface.latency_ms,
                rate_mbps=iface.bandwidth_mbps,
            ),
        ]
        if iface.locality == node_agent_pb2.LOCALITY_LOCAL:
            host_a = ground_bridge._isl_host_name(
                iface.node_id, ground_bridge._isl_idx_from_ifname(iface.interface_name)
            )
            host_b = ground_bridge._isl_host_name(
                iface.peer_node_id, ground_bridge._isl_idx_from_ifname(iface.peer_interface_name)
            )
            proofs.extend(
                [
                    kernel_verifier.verify_host_interface_state(host_a, admin_up=True),
                    kernel_verifier.verify_host_interface_state(host_b, admin_up=True),
                ]
            )
        return _combine_proofs("ISL LinkUp verified", proofs)
    except Exception as exc:
        msg = f"ISL up carrier stage failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def handle_batch_link_up(
    request: node_agent_pb2.BatchLinkUpRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.BatchLinkUpResponse:
    """Handle BatchLinkUp command.

    Preparation stage (sequential): create VXLAN tunnels for CROSS_NODE interfaces.
    Carrier stage (concurrent): bring host-side veth carrier UP and apply
      tc tbf + tc netem shaping on every interface. ACK as soon as this stage
      is complete — Layer 2 carrier transitions are the Node Agent's
      contract with the Scheduler.

    Layer 3 neighbor resolution (NDP) is NOT performed. Per PRD §13.22.6
    (v0.72 reversal), NDP is owned by FRR (IGP sessions — IS-IS/OSPF hello
    fires NS naturally on carrier-up) and the kernel NDP state machine
    (NodalPath sessions — resolves on first forwarding attempt). The
    previous neighbor-discovery nsenter-ping storm and its `trigger_ndp_and_wait`
    polling loop are deleted: a Layer 2 substrate actuator must not do
    Layer 3 work, the subprocess.run-per-link pattern did not scale, and
    the per-attempt `_ns_lock` contention defeated the entire simultaneity
    goal.
    """
    start = _time.monotonic()
    outcomes: dict[tuple[str, str], EntryOutcome] = {}
    upped = 0

    if fence is None:
        raise ValueError("RuntimeFence is required")
    try:
        validate_batch_link_up_request(request, fence=fence)
    except CommandContractError as exc:
        ops_events.publish(
            level="warning",
            code="COMMAND_REJECTED",
            message=f"BatchLinkUp rejected: {exc.message}",
            session_id=fence.session_id,
            details={
                "operation_id": request.envelope.operation_id,
                "wiring_generation": fence.wiring_generation,
                "command_type": "BatchLinkUp",
                "error_code": _error_code_name(exc.code),
            },
        )
        return _batch_up_failure(request, code=exc.code, message=exc.message)

    if pid_map is None:
        raise ValueError("pid_map is None — wiring not complete?")
    pm = pid_map

    # Validate PIDs before mutation. Per-interface locality:
    # LOCAL GROUND needs both gs_id and sat_id PIDs (bridge ops).
    # CROSS_NODE needs only node_id PID. LOCAL ISL needs node_id PID.
    for iface in request.interfaces:
        try:
            if (
                iface.link_type == node_agent_pb2.LINK_TYPE_GROUND
                and iface.locality == node_agent_pb2.LOCALITY_LOCAL
            ):
                _require_pid(iface.gs_id, pm)
                _require_pid(iface.sat_id, pm)
            else:
                _require_pid(iface.node_id, pm)
        except Exception as exc:
            return _batch_up_failure(
                request,
                code=node_agent_pb2.NODE_AGENT_PID_NOT_FOUND,
                message=str(exc),
            )

    # Preparation stage: Create VXLAN tunnels for CROSS_NODE interfaces.
    # Each interface carries its own locality — only CROSS_NODE interfaces
    # need VXLAN. LOCAL interfaces in the same batch are unaffected.
    #
    # ISL CROSS_NODE: create VXLAN + veth pair, move veth into pod namespace
    # GROUND CROSS_NODE: create VXLAN in host ns, tc mirred to existing host-side
    #   interface (satellite _gnd_{sat} or GS _gbr-{gs}). Pod-side gnd0 already
    #   exists from wiring. Same carrier model as LOCAL ground links.
    local_ip: str | None = None
    preparation_failed: set[tuple[str, str]] = set()
    for iface in request.interfaces:
        if iface.locality != node_agent_pb2.LOCALITY_CROSS_NODE:
            continue
        try:
            if local_ip is None:
                local_ip = _discover_local_ip()
        except Exception as exc:
            outcome = _fail(node_agent_pb2.NODE_AGENT_HOST_IP_MISSING, str(exc))
            outcomes[_iface_key(iface)] = outcome
            preparation_failed.add(_iface_key(iface))
            continue

        try:
            from node_agent import substrate_monitor

            substrate_monitor.require_fresh_measurement_for_remote_ip(iface.remote_node_ip)
        except Exception as exc:
            outcomes[_iface_key(iface)] = _fail(
                node_agent_pb2.NODE_AGENT_DEPENDENCY_MISSING,
                f"Substrate measurement unavailable for {iface.remote_node_ip}: {exc}",
                dirty_kernel=False,
            )
            preparation_failed.add(_iface_key(iface))
            continue

        if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
            try:
                gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
                is_sat = iface.node_id == iface.sat_id
                if is_sat:
                    host_ifname = ground_bridge._sat_host_veth(iface.node_id, sat_ifname)
                    sat_pid = _require_pid(iface.node_id, pm)
                else:
                    host_ifname = ground_bridge._gs_host_veth(iface.node_id, gs_ifname)
                    sat_pid = None
                local_pid = _require_pid(iface.node_id, pm)
                vxlan.attach_cross_node_ground(
                    local_host_ifname=host_ifname,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                    sat_pid=sat_pid if is_sat else None,
                    sat_ifname=sat_ifname,
                )
                namespace_ops.apply_link_shaping(
                    local_pid,
                    iface.interface_name,
                    iface.latency_ms,
                    iface.bandwidth_mbps,
                )
                substrate_monitor.add_peer_ref(
                    substrate_monitor.PeerRef(
                        session_id=fence.session_id,
                        wiring_generation=fence.wiring_generation,
                        remote_ip=iface.remote_node_ip,
                        vni=iface.vni,
                        local_ifname=iface.interface_name,
                    )
                )
                vxlan_if, _, _ = vxlan._host_ifnames(iface.vni)
                outcomes[_iface_key(iface)] = _combine_proofs(
                    "CROSS_NODE ground LinkUp verified",
                    [
                        kernel_verifier.verify_vxlan(
                            iface.vni, local_ip=local_ip, remote_ip=iface.remote_node_ip
                        ),
                        kernel_verifier.verify_mirred(vxlan_if, host_ifname),
                        kernel_verifier.verify_mirred(host_ifname, vxlan_if),
                        kernel_verifier.verify_qdisc(
                            local_pid,
                            iface.interface_name,
                            delay_ms=iface.latency_ms,
                            rate_mbps=iface.bandwidth_mbps,
                        ),
                    ],
                )
            except Exception as exc:
                msg = f"VXLAN ground attach failed {iface.node_id}/{iface.interface_name}: {exc}"
                outcomes[_iface_key(iface)] = _fail(
                    node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
                    msg,
                    dirty_kernel=True,
                )
                preparation_failed.add(_iface_key(iface))
        else:
            # ISL: create full VXLAN + veth pair into pod namespace
            try:
                pid = _require_pid(iface.node_id, pm)
                vxlan.create_vxlan_link(
                    pid=pid,
                    ifname=iface.interface_name,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                )
                substrate_monitor.add_peer_ref(
                    substrate_monitor.PeerRef(
                        session_id=fence.session_id,
                        wiring_generation=fence.wiring_generation,
                        remote_ip=iface.remote_node_ip,
                        vni=iface.vni,
                        local_ifname=iface.interface_name,
                    )
                )
                proof = kernel_verifier.verify_vxlan(
                    iface.vni, local_ip=local_ip, remote_ip=iface.remote_node_ip
                )
                if not proof.verified:
                    outcomes[_iface_key(iface)] = _ok(proof)
                    preparation_failed.add(_iface_key(iface))
            except Exception as exc:
                msg = f"VXLAN create failed {iface.node_id}/{iface.interface_name}: {exc}"
                outcomes[_iface_key(iface)] = _fail(
                    node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
                    msg,
                    dirty_kernel=True,
                )
                preparation_failed.add(_iface_key(iface))

    # Carrier stage: Bring interfaces UP + apply shaping.
    # - LOCAL GROUND: bridge + mirred attach (_ground_link_up)
    # - CROSS_NODE GROUND: VXLAN attach + local endpoint shaping handled above
    # - LOCAL/CROSS_NODE ISL: UP + shaping (_isl_link_up_carrier_stage)
    carrier_futures = {}
    for iface in request.interfaces:
        if _iface_key(iface) in preparation_failed:
            continue
        if (
            iface.link_type == node_agent_pb2.LINK_TYPE_GROUND
            and iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE
        ):
            continue
        if (
            iface.link_type == node_agent_pb2.LINK_TYPE_GROUND
            and iface.locality == node_agent_pb2.LOCALITY_LOCAL
        ):
            carrier_futures[_BATCH_POOL.submit(_ground_link_up, iface, pm)] = iface
        else:
            carrier_futures[_BATCH_POOL.submit(_isl_link_up_carrier_stage, iface, pm)] = iface

    # Wait for all carrier-stage work to complete. ACK on completion — Layer 3
    # neighbor resolution happens asynchronously in FRR / kernel NDP
    # (see docstring and PRD §13.22.6).
    for fut in as_completed(carrier_futures):
        iface = carrier_futures[fut]
        try:
            outcome = fut.result(timeout=10)
            if outcome.success:
                upped += 1
            outcomes[_iface_key(iface)] = outcome
        except Exception as exc:
            msg = f"Carrier-stage error for {iface.node_id}/{iface.interface_name}: {exc}"
            outcomes[_iface_key(iface)] = _fail(
                node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
                msg,
                dirty_kernel=True,
            )

    upped += sum(
        1
        for iface in request.interfaces
        if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND
        and iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE
        and outcomes.get(
            _iface_key(iface), EntryOutcome(error_code=node_agent_pb2.NODE_AGENT_INTERNAL_ERROR)
        ).success
    )

    elapsed = (_time.monotonic() - start) * 1000
    ordered_outcomes = [
        outcomes.get(
            _iface_key(iface),
            _fail(node_agent_pb2.NODE_AGENT_INTERNAL_ERROR, "not attempted"),
        )
        for iface in request.interfaces
    ]
    errors = [outcome.error_message for outcome in ordered_outcomes if not outcome.success]
    error_msg = "; ".join(errors)
    success = all(outcome.success for outcome in ordered_outcomes)
    if not success:
        ifaces = ", ".join(f"{i.node_id}/{i.interface_name}" for i in request.interfaces)
        log.warning(
            "BatchLinkUp: %d/%d upped (%.1fms) [%s]: %s",
            upped,
            len(request.interfaces),
            elapsed,
            ifaces,
            error_msg,
        )
    else:
        ifaces = ", ".join(f"{i.node_id}/{i.interface_name}" for i in request.interfaces)
        log.debug("BatchLinkUp: %d upped (%.1fms) [%s]", upped, elapsed, ifaces)

    _publish_command_event(
        operation="BatchLinkUp",
        envelope=request.envelope,
        outcomes=ordered_outcomes,
    )
    return node_agent_pb2.BatchLinkUpResponse(
        success=success,
        error_code=_aggregate_response_code(ordered_outcomes),
        error_message=error_msg,
        dirty_kernel=_dirty(ordered_outcomes),
        interfaces_upped=upped,
        apply_time_ms=elapsed,
        interface_results=[
            _interface_result(iface, outcome)
            for iface, outcome in zip(request.interfaces, ordered_outcomes, strict=True)
        ],
    )


def _kernel_inventory_entry_result(
    entry, outcome: EntryOutcome
) -> node_agent_pb2.KernelInventoryEntryResult:
    return node_agent_pb2.KernelInventoryEntryResult(
        node_id=entry.node_id,
        interface_name=entry.interface_name,
        success=outcome.success,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        verified=outcome.verified,
        dirty_kernel=outcome.dirty_kernel,
        proof_summary=outcome.proof_summary,
        proof_evidence=list(outcome.proof_evidence),
    )


def _verify_kernel_inventory_entry(
    entry: node_agent_pb2.KernelInventoryEntry,
    pid_map: dict[str, int] | None = None,
) -> EntryOutcome:
    """Read-only GS-facing verification for actuation recovery."""
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pm = pid_map
        gs_ifname, sat_ifname = _extract_ground_ifaces(entry)
        is_sat = entry.node_id == entry.sat_id
        local_pid = _require_pid(entry.node_id, pm)
        host_ifname = (
            ground_bridge._sat_host_veth(entry.sat_id, sat_ifname)
            if is_sat
            else ground_bridge._gs_host_veth(entry.gs_id, gs_ifname)
        )
        local_ifname = sat_ifname if is_sat else gs_ifname

        if entry.expected_admin_up:
            proofs = [
                kernel_verifier.verify_host_interface_state(host_ifname, admin_up=True),
                kernel_verifier.verify_qdisc(
                    local_pid,
                    local_ifname,
                    delay_ms=entry.latency_ms,
                    rate_mbps=entry.bandwidth_mbps,
                ),
            ]
            if entry.locality == node_agent_pb2.LOCALITY_LOCAL:
                # LOCAL ground links are verified on the one agent that owns
                # both pod namespaces and both host-side veths.
                gs_pid = _require_pid(entry.gs_id, pm)
                sat_pid = _require_pid(entry.sat_id, pm)
                gs_port = ground_bridge._gs_host_veth(entry.gs_id, gs_ifname)
                sat_host = ground_bridge._sat_host_veth(entry.sat_id, sat_ifname)
                proofs.extend(
                    [
                        kernel_verifier.verify_host_interface_state(gs_port, admin_up=True),
                        kernel_verifier.verify_host_interface_state(sat_host, admin_up=True),
                        kernel_verifier.verify_mirred(gs_port, sat_host),
                        kernel_verifier.verify_mirred(sat_host, gs_port),
                        kernel_verifier.verify_qdisc(
                            gs_pid,
                            gs_ifname,
                            delay_ms=entry.latency_ms,
                            rate_mbps=entry.bandwidth_mbps,
                        ),
                        kernel_verifier.verify_qdisc(
                            sat_pid,
                            sat_ifname,
                            delay_ms=entry.latency_ms,
                            rate_mbps=entry.bandwidth_mbps,
                        ),
                    ]
                )
            elif entry.vni:
                local_ip = _discover_local_ip()
                vxlan_if, _, _ = vxlan._host_ifnames(entry.vni)
                proofs.extend(
                    [
                        kernel_verifier.verify_vxlan(
                            entry.vni, local_ip=local_ip, remote_ip=entry.remote_node_ip
                        ),
                        kernel_verifier.verify_mirred(host_ifname, vxlan_if),
                        kernel_verifier.verify_mirred(vxlan_if, host_ifname),
                    ]
                )
            return _combine_proofs("KernelInventory expected-up verified", proofs)

        proofs = [
            kernel_verifier.verify_host_interface_state(host_ifname, admin_up=False),
            kernel_verifier.verify_pod_interface_exists(local_pid, local_ifname),
        ]
        if entry.locality == node_agent_pb2.LOCALITY_LOCAL:
            sat_pid = _require_pid(entry.sat_id, pm)
            gs_port = ground_bridge._gs_host_veth(entry.gs_id, gs_ifname)
            sat_host = ground_bridge._sat_host_veth(entry.sat_id, sat_ifname)
            proofs.extend(
                [
                    kernel_verifier.verify_host_interface_state(gs_port, admin_up=False),
                    kernel_verifier.verify_host_interface_state(sat_host, admin_up=False),
                    kernel_verifier.verify_pod_interface_exists(sat_pid, sat_ifname),
                ]
            )
        elif entry.vni:
            proofs.append(kernel_verifier.verify_vxlan_absent(entry.vni))
        return _combine_proofs("KernelInventory expected-down verified", proofs)
    except Exception as exc:
        msg = f"KernelInventory failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED, msg, dirty_kernel=True)


def handle_kernel_inventory(
    request: node_agent_pb2.KernelInventoryRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.KernelInventoryResponse:
    """Handle read-only KernelInventory verification."""
    start = _time.monotonic()
    if fence is None:
        raise ValueError("RuntimeFence is required")
    try:
        validate_kernel_inventory_request(request, fence=fence)
    except CommandContractError as exc:
        ops_events.publish(
            level="warning",
            code="COMMAND_REJECTED",
            message=f"KernelInventory rejected: {exc.message}",
            session_id=fence.session_id,
            details={
                "operation_id": request.envelope.operation_id,
                "wiring_generation": fence.wiring_generation,
                "command_type": "KernelInventory",
                "error_code": _error_code_name(exc.code),
            },
        )
        return node_agent_pb2.KernelInventoryResponse(
            success=False,
            error_code=exc.code,
            error_message=exc.message,
            dirty_kernel=False,
        )

    if pid_map is None:
        raise ValueError("pid_map is None — wiring not complete?")

    futures = {
        _BATCH_POOL.submit(_verify_kernel_inventory_entry, entry, pid_map): entry
        for entry in request.entries
    }
    outcomes: dict[tuple[str, str], EntryOutcome] = {}
    for fut in as_completed(futures):
        entry = futures[fut]
        try:
            outcomes[_iface_key(entry)] = fut.result(timeout=10)
        except Exception as exc:
            outcomes[_iface_key(entry)] = _fail(
                node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
                f"KernelInventory exception for {entry.node_id}/{entry.interface_name}: {exc}",
                dirty_kernel=True,
            )

    ordered_outcomes = [
        outcomes.get(
            _iface_key(entry),
            _fail(node_agent_pb2.NODE_AGENT_INTERNAL_ERROR, "not attempted", dirty_kernel=True),
        )
        for entry in request.entries
    ]
    elapsed = (_time.monotonic() - start) * 1000
    errors = [outcome.error_message for outcome in ordered_outcomes if not outcome.success]
    error_msg = "; ".join(errors)
    success = all(outcome.success for outcome in ordered_outcomes)
    _publish_command_event(
        operation="KernelInventory",
        envelope=request.envelope,
        outcomes=ordered_outcomes,
    )
    return node_agent_pb2.KernelInventoryResponse(
        success=success,
        error_code=_aggregate_response_code(ordered_outcomes),
        error_message=error_msg,
        dirty_kernel=_dirty(ordered_outcomes),
        entries_verified=sum(1 for outcome in ordered_outcomes if outcome.success),
        apply_time_ms=elapsed,
        entry_results=[
            _kernel_inventory_entry_result(entry, outcome)
            for entry, outcome in zip(request.entries, ordered_outcomes, strict=True)
        ],
    )


def handle_set_latency(
    request: node_agent_pb2.SetLatencyRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.SetLatencyResponse:
    """Handle SetLatency command.

    Updates tc netem delay on existing qdisc chains. Does NOT change
    admin state, re-add qdiscs, or touch anything other than the netem
    delay parameter.
    """
    if fence is None:
        raise ValueError("RuntimeFence is required")
    try:
        validate_set_latency_request(request, fence=fence)
    except CommandContractError as exc:
        ops_events.publish(
            level="warning",
            code="COMMAND_REJECTED",
            message=f"SetLatency rejected: {exc.message}",
            session_id=fence.session_id,
            details={
                "operation_id": request.envelope.operation_id,
                "wiring_generation": fence.wiring_generation,
                "command_type": "SetLatency",
                "error_code": _error_code_name(exc.code),
            },
        )
        return _set_latency_failure(request, code=exc.code, message=exc.message)

    outcomes: list[EntryOutcome] = []
    updated = 0

    for entry in request.entries:
        outcome = _update_latency_entry(entry, pid_map)
        outcomes.append(outcome)
        if outcome.success:
            updated += 1

    errors = [outcome.error_message for outcome in outcomes if not outcome.success]
    error_msg = "; ".join(errors)
    success = all(outcome.success for outcome in outcomes)
    _publish_command_event(
        operation="SetLatency",
        envelope=request.envelope,
        outcomes=outcomes,
    )
    return node_agent_pb2.SetLatencyResponse(
        success=success,
        error_code=_aggregate_response_code(outcomes),
        error_message=error_msg,
        dirty_kernel=_dirty(outcomes),
        entries_updated=updated,
        entry_results=[
            _latency_result(entry, outcome)
            for entry, outcome in zip(request.entries, outcomes, strict=True)
        ],
    )
