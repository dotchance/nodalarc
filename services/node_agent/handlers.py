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
from dataclasses import dataclass, replace

from nodalarc.proto import node_agent_pb2
from nodalarc.runtime_naming import link_interface_mac, vxlan_host_ifnames
from nodalarc.substrate.queue_sizing import queue_limit_packets

from node_agent import (
    admission,
    ground_bridge,
    kernel_actuator,
    kernel_verifier,
    loss,
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
from node_agent.mpls import configure_mpls_input
from node_agent.operation_executor import execute_plan
from node_agent.operation_plan import OperationPlan, OperationStep
from node_agent.pid_discovery import NamespaceHandle, verify_handle

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
    """Raised when a node_id has no valid namespace handle."""


def _require_pid(node_id: str, handles: dict[str, NamespaceHandle]) -> int:
    """Resolve a node's namespace PID through its validated handle.

    Verifies the handle still names the exact network namespace it was
    created for before every kernel mutation: a recreated sandbox keeps the
    pod but destroys the wired namespace, and mutating a stale or reused
    PID would operate on a namespace this agent never wired.
    """
    handle = handles.get(node_id)
    if handle is None:
        raise PidNotFoundError(
            f"node_id '{node_id}' has no namespace handle — pod not wired or not on this node"
        )
    if not verify_handle(handle):
        raise PidNotFoundError(
            f"node_id '{node_id}' namespace changed since wiring (sandbox replaced) — "
            "rewire required"
        )
    return handle.pid


def _requires_mpls(node_id: str, handles: dict[str, NamespaceHandle]) -> bool:
    """The manifest's MPLS requirement for a node, as bound to its published handle."""
    return handles[node_id].mpls_enable


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
    # What the end lost, on entries that close it.
    loss: node_agent_pb2.LinkEndLoss | None = None
    # A stage for a contact the end no longer carries: it changed nothing.
    superseded: bool = False
    # A survey's report: (carried contact or "", admission stopped, receive queue delay in ms,
    # the carried contact's peer end "node/interface" or "").
    survey: tuple[str, bool, float, str] | None = None

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
        # The agent is the component that OBSERVED the divergence; it must
        # say so in its own log, not only in the reply. Without this, a
        # failed proof is invisible on the node where the evidence lives.
        log.warning(
            "Kernel proof failed: %s — %s",
            summary,
            "; ".join(proof.summary for proof in failures),
            extra={"evidence": list(evidence)[:8]},
        )
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
    if not outcome.success:
        # The agent's own log carries who/what/why for every failed
        # interface outcome: dirty-kernel failures are errors (the kernel
        # now needs recovery), everything else warns. Success is debug-only.
        log.log(
            logging.ERROR if outcome.dirty_kernel else logging.WARNING,
            "%s/%s failed (code=%s%s): %s",
            iface.node_id,
            iface.interface_name,
            outcome.error_code,
            ", dirty_kernel" if outcome.dirty_kernel else "",
            outcome.error_message,
        )
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
        loss=outcome.loss,
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
    iface: node_agent_pb2.InterfaceDown, handles: dict[str, NamespaceHandle] | None = None
) -> EntryOutcome:
    """Deactivate an ISL link.

    LOCAL ISLs: detaches host-side veths (brings host-side DOWN → carrier
    drops on pod-side). Pod-side interface remains admin UP in LOWERLAYERDOWN
    state. Pod-side tc qdiscs are left in place — apply_transmit_shaping uses
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


def _end_host_ifname(entry) -> str:
    """The host-side veth through which a pod link interface receives, for any entry naming one
    end. An ISL end's is its mediated veth (local) or its VXLAN veth (cross-node); a ground end's
    is the station's bridge port or the satellite's host veth, in both localities (a cross-node
    ground link joins that veth to its tunnel)."""
    if entry.link_type == node_agent_pb2.LINK_TYPE_GROUND:
        if entry.node_id == entry.gs_id:
            return ground_bridge._gs_host_veth(entry.gs_id, entry.interface_name)
        if entry.node_id == entry.sat_id:
            return ground_bridge._sat_host_veth(entry.sat_id, entry.interface_name)
        raise ValueError(
            f"ground entry names {entry.node_id!r}, neither its station {entry.gs_id!r} nor its "
            f"satellite {entry.sat_id!r}"
        )
    if entry.locality == node_agent_pb2.LOCALITY_CROSS_NODE:
        return vxlan_host_ifnames(entry.vni).host_veth
    return ground_bridge._isl_host_name(
        entry.node_id, ground_bridge._isl_idx_from_ifname(entry.interface_name)
    )


def _host_ifname_down(iface: node_agent_pb2.InterfaceDown) -> str:
    """The host-side veth that feeds a pod link interface, for a LinkDown entry."""
    return _end_host_ifname(iface)


def _long_delay_entry(entry, handles: dict[str, NamespaceHandle]) -> bool:
    """Whether an entry names one end of a long-delay link: a fixed link the manifest classes
    long-delay, or an access link to a peer the manifest lists as a long-delay access peer. A
    fixed interface's class is the session's; an access interface's is its peer's, per pass."""
    handle = handles.get(entry.node_id)
    if handle is None:
        return False
    if entry.link_type == node_agent_pb2.LINK_TYPE_GROUND:
        peer = entry.sat_id if entry.node_id == entry.gs_id else entry.gs_id
        return handle.long_delay_access_peer(peer)
    return handle.is_long_delay(entry.interface_name)


def _set_host_down(host_ifname: str) -> None:
    def _op(ipr) -> None:
        links = ipr.link_lookup(ifname=host_ifname)
        if not links:
            raise FileNotFoundError(f"Host interface {host_ifname} not found")
        ipr.link("set", index=links[0], state="down")

    namespace_ops.in_host_namespace(_op)


def _superseded(iface: node_agent_pb2.InterfaceDown, host_ifname: str) -> EntryOutcome | None:
    """The outcome of a stage for a contact this end no longer carries, or None when it carries
    that contact. A stage for an earlier contact changes nothing of the contact the end carries
    now. A close of an end that carries none repeats a close that already ran. A stop of an end
    that carries none cannot be proven: the end's contact is gone from the kernel."""
    carried = namespace_ops.contact_of(host_ifname)
    if carried == iface.contact_id:
        return None
    end = f"{iface.node_id}/{iface.interface_name}"
    if not carried and iface.stage == node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION:
        return _fail(
            node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
            f"{end} carries no contact (host-side {host_ifname} absent or untagged); the stop of "
            f"contact {iface.contact_id} cannot be proven",
            dirty_kernel=True,
        )
    summary = (
        f"{end} carries contact {carried}, not {iface.contact_id}"
        if carried
        else f"{end} carries no contact: already closed"
    )
    return replace(
        _ok(kernel_verifier.Proof.ok(summary, f"host_ifname={host_ifname}")), superseded=True
    )


def _since_reported(count: int, reported: int) -> int:
    """What a kernel counter counted since the end last reported it. A counter only grows, so a
    count below the reported value belongs to a counter created after the report (a new
    admission filter, a fresh receive queue): all of it is new."""
    return count - reported if count >= reported else count


def _stop_long_delay_admission(
    iface: node_agent_pb2.InterfaceDown, handles: dict[str, NamespaceHandle]
) -> EntryOutcome:
    """STOP_ADMISSION on a long-delay end: its transmitting interface admits nothing more, and
    what waited behind its rate shaper is purged and counted, never transmitted before the
    contact ended. Its receive queue and delivery path stay, so packets already transmitted keep
    arriving. The acknowledgement proves admission stopped and the transmit queue empty."""
    try:
        pid = _require_pid(iface.node_id, handles)
        host_ifname = _host_ifname_down(iface)
        superseded = _superseded(iface, host_ifname)
        if superseded is not None:
            return superseded
        tag = namespace_ops.contact_tag(host_ifname)
        if tag is None:
            raise RuntimeError(f"{host_ifname} lost its contact tag during the stop")
        admission.stop_admission(pid, iface.interface_name)
        purge = admission.purge_untransmitted(pid, iface.interface_name)
        # The transmit queue's drops are now reported up to the purge, and the purge itself.
        namespace_ops.tag_contact(
            host_ifname,
            replace(tag, transmit_dropped_reported=purge.dropped_before + purge.purged),
        )
        outcome = _combine_proofs(
            "long-delay admission stopped, untransmitted backlog purged",
            [
                admission.verify_admission_hook(pid, iface.interface_name, stopped=True),
                kernel_verifier.verify_transmit_queue_empty(pid, iface.interface_name),
                kernel_verifier.verify_contact(host_ifname, iface.contact_id),
            ],
        )
        return replace(
            outcome,
            loss=node_agent_pb2.LinkEndLoss(
                untransmitted_at_stop=purge.purged,
                transmit_dropped=_since_reported(
                    purge.dropped_before, tag.transmit_dropped_reported
                ),
                discarded_exact=True,
            ),
        )
    except Exception as exc:
        msg = f"stop admission failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _close_long_delay_end(
    iface: node_agent_pb2.InterfaceDown, handles: dict[str, NamespaceHandle]
) -> EntryOutcome:
    """CLOSE on a long-delay end: its receive queue is reset by bringing its host-side veth down,
    and what the queue still held is counted exactly (both ends stopped admitting before the
    close, so nothing enters it); then the end's link resources go. Reports what the end lost
    since its contact's last report, from the counts its contact tag records."""
    try:
        pid = _require_pid(iface.node_id, handles)
        host_ifname = _host_ifname_down(iface)
        superseded = _superseded(iface, host_ifname)
        if superseded is not None:
            return superseded
        tag = namespace_ops.contact_tag(host_ifname)
        if tag is None:
            raise RuntimeError(f"{host_ifname} lost its contact tag during the close")
        state = admission.read_admission(pid, iface.interface_name)
        if not state.stopped or state.taken is None:
            # The close counts what the queue holds while nothing enters it; with admission
            # open, packets enter between the count and the reset.
            return _fail(
                node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
                f"close of {iface.node_id}/{iface.interface_name}: its admission is not stopped",
                dirty_kernel=True,
            )
        transmit_dropped = loss.QueueDevice(iface.interface_name, pid).counts().dropped
        device = loss.QueueDevice(host_ifname, None)
        before = device.counts()
        (discarded,) = loss.counted_reset([device], lambda: _set_host_down(host_ifname))
        if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
            proofs = _close_ground_end(iface, handles, pid, host_ifname)
        elif iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE:
            vxlan.destroy_vxlan_link(pid, iface.interface_name, iface.vni)
            proofs = [kernel_verifier.verify_vxlan_absent(iface.vni)]
        else:
            namespace_ops.tag_contact(host_ifname, None)
            proofs = [kernel_verifier.verify_host_interface_state(host_ifname, admin_up=False)]
        outcome = _combine_proofs("long-delay end closed", proofs)
        return replace(
            outcome,
            loss=node_agent_pb2.LinkEndLoss(
                admission_taken=_since_reported(state.taken, tag.taken_reported),
                queue_dropped=_since_reported(before.dropped, tag.dropped_reported),
                transmit_dropped=_since_reported(transmit_dropped, tag.transmit_dropped_reported),
                discarded_at_close=discarded,
                discarded_exact=True,
            ),
        )
    except Exception as exc:
        msg = f"close failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _pin_access_neighbors(
    node_id: str,
    ifname: str,
    peer_node_id: str,
    peer_ifname: str,
    handles: dict[str, NamespaceHandle],
) -> list[kernel_verifier.Proof]:
    """On a long-delay access link, pin the peer's addresses to the peer access interface's link
    address on this end, as a fixed long-delay link does: address resolution cannot complete
    across it. The peer is chosen per pass, so the pin is made at each ground LinkUp. Returns
    the proofs of this end's own link address and the pins; none on a local access link."""
    neighbors = handles[node_id].access_neighbors_for(peer_node_id, peer_ifname)
    if neighbors is None:
        return []
    pid = _require_pid(node_id, handles)
    namespace_ops.install_static_neighbors(pid, ifname, neighbors.lladdr, neighbors.addresses)
    return [
        kernel_verifier.verify_link_mac(pid, ifname, link_interface_mac(node_id, ifname)),
        kernel_verifier.verify_static_neighbors(pid, ifname, neighbors.lladdr, neighbors.addresses),
    ]


def _unpin_access_neighbors(
    node_id: str,
    ifname: str,
    peer_node_id: str,
    peer_ifname: str,
    handles: dict[str, NamespaceHandle],
) -> list[kernel_verifier.Proof]:
    """Remove a long-delay access link's pins at its ground LinkDown, and prove them gone: the
    interface's next pass may link it to another peer."""
    neighbors = handles[node_id].access_neighbors_for(peer_node_id, peer_ifname)
    if neighbors is None:
        return []
    pid = _require_pid(node_id, handles)
    namespace_ops.remove_static_neighbors(pid, ifname, neighbors.addresses)
    return [kernel_verifier.verify_static_neighbors_absent(pid, ifname, neighbors.addresses)]


def _close_ground_end(
    iface: node_agent_pb2.InterfaceDown,
    handles: dict[str, NamespaceHandle],
    pid: int,
    host_ifname: str,
) -> list[kernel_verifier.Proof]:
    """The teardown of one long-delay ground end after its receive queue was reset: its outgoing
    redirect goes (local: the host veth's ingress redirect toward its peer; cross-node: the
    tunnel), a satellite's terminal interface goes down, the contact is cleared from its host
    veth (which lives for the session and serves its next pass) and its pins are removed. The
    peer end keeps draining: its queue is on its own host veth."""
    is_sat = iface.node_id == iface.sat_id
    if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE:
        vxlan.detach_cross_node_ground(
            host_ifname, iface.vni, pid if is_sat else None, iface.interface_name
        )
        proofs = [kernel_verifier.verify_vxlan_absent(iface.vni)]
    else:
        ground_bridge.remove_redirect(iface.gs_id, host_ifname)
        if is_sat:
            namespace_ops.set_pod_interface_down(pid, iface.interface_name)
        proofs = [kernel_verifier.verify_host_interface_state(host_ifname, admin_up=False)]
    namespace_ops.tag_contact(host_ifname, None)
    proofs.extend(
        _unpin_access_neighbors(
            iface.node_id,
            iface.interface_name,
            iface.peer_node_id,
            iface.peer_interface_name,
            handles,
        )
    )
    return proofs


def _ground_long_delay_stage(
    iface: node_agent_pb2.InterfaceUp,
    handles: dict[str, NamespaceHandle],
    fence: RuntimeFence,
) -> EntryOutcome:
    """PREPARE or OPEN of one end of a long-delay ground contact: the stages a long-delay fixed
    link's end runs, on the ground end's pod interface and host veth. PREPARE stops admission
    before the path can forward, joins the path (the shared redirect pair on one host, the
    tunnel across hosts), builds the end's transmit shaping, receive queue and pins, and records
    the contact. OPEN admits."""
    try:
        pid = _require_pid(iface.node_id, handles)
        host_ifname = _end_host_ifname(iface)
        if iface.stage == node_agent_pb2.LINK_UP_STAGE_OPEN:
            return _open_prepared_contact(pid, host_ifname, iface)
        admission.stop_admission(pid, iface.interface_name)
        is_sat = iface.node_id == iface.sat_id
        gs_ifname = iface.peer_interface_name if is_sat else iface.interface_name
        sat_ifname = iface.interface_name if is_sat else iface.peer_interface_name
        if iface.locality == node_agent_pb2.LOCALITY_LOCAL:
            # Each end joins its own direction; the peer end's PREPARE joins the other, and the
            # barrier opens neither before both.
            gs_port = ground_bridge._gs_host_veth(iface.gs_id, gs_ifname)
            sat_host = ground_bridge._sat_host_veth(iface.sat_id, sat_ifname)
            peer_host = gs_port if is_sat else sat_host
            ground_bridge.attach_ground_end(
                iface.gs_id, host_ifname, peer_host, pid if is_sat else None, sat_ifname
            )
            path = [
                kernel_verifier.verify_host_interface_state(host_ifname, admin_up=True),
                kernel_verifier.verify_host_interface_state(peer_host, admin_up=True),
                kernel_verifier.verify_mirred(host_ifname, peer_host),
            ]
        else:
            from node_agent import substrate_monitor

            local_ip = _discover_local_ip()
            substrate_monitor.require_fresh_measurement_for_remote_ip(iface.remote_node_ip)
            vxlan.attach_cross_node_ground(
                local_host_ifname=host_ifname,
                local_ip=local_ip,
                remote_ip=iface.remote_node_ip,
                vni=iface.vni,
                sat_pid=pid if is_sat else None,
                sat_ifname=sat_ifname,
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
            tunnel = vxlan_host_ifnames(iface.vni).tunnel
            path = [
                kernel_verifier.verify_vxlan(
                    iface.vni, local_ip=local_ip, remote_ip=iface.remote_node_ip
                ),
                kernel_verifier.verify_mirred(tunnel, host_ifname),
                kernel_verifier.verify_mirred(host_ifname, tunnel),
            ]
        proofs, ended = _prepare_long_delay_contact(pid, host_ifname, iface)
        proofs = [*path, *proofs]
        proofs.extend(
            _pin_access_neighbors(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
                handles,
            )
        )
        if _requires_mpls(iface.node_id, handles):
            proofs.append(kernel_verifier.verify_mpls_input(pid, iface.interface_name))
        outcome = _combine_proofs("long-delay ground PREPARE verified", proofs)
        return replace(outcome, loss=ended) if ended is not None else outcome
    except Exception as exc:
        msg = f"long-delay ground stage failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _admit_local_pass(node_id: str, ifname: str, handles: dict[str, NamespaceHandle]) -> None:
    """A local pass on an access interface that can carry long-delay passes: the admission hook
    wiring installed (stopped), or a closed long-delay pass left stopped, opens. The shaping the
    local pass applies replaces whatever layout the interface carried."""
    if not handles[node_id].carries_long_delay_access:
        return
    pid = _require_pid(node_id, handles)
    admission.freeze_admission(pid, ifname)
    admission.open_admission(pid, ifname)


def _ground_link_down(
    iface: node_agent_pb2.InterfaceDown, handles: dict[str, NamespaceHandle] | None = None
) -> EntryOutcome:
    """Tear down a ground link.

    Ground LinkDown sequence:
    1. Remove tc mirred redirect from host-side veths
    2. Bring satellite host-side veth DOWN — carrier drops on GS gnd0
       automatically (UP → LOWERLAYERDOWN), FRR tears down adjacency
    3. Bring satellite gnd0 DOWN

    Pod-side tc qdiscs on both gnd0 interfaces are left in place —
    apply_transmit_shaping uses replace semantics on the next LinkUp.

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.
    """
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pm = handles
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
                *_unpin_access_neighbors(iface.gs_id, gs_ifname, iface.sat_id, sat_ifname, pm),
                *_unpin_access_neighbors(iface.sat_id, sat_ifname, iface.gs_id, gs_ifname, pm),
            ],
        )
    except Exception as exc:
        msg = f"Ground down failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _cross_node_ground_down(
    iface: node_agent_pb2.InterfaceDown,
    handles: dict[str, NamespaceHandle],
    host_ifname: str,
    sat_pid: int | None,
    sat_ifname: str,
) -> EntryOutcome | None:
    """This host's end of a cross-node ground LinkDown: the VXLAN detach, then on a long-delay
    access link the removal of the pins this end made. None when there are no pins: the caller
    proves the VXLAN gone, as for every cross-node detach."""
    vxlan.detach_cross_node_ground(host_ifname, iface.vni, sat_pid, sat_ifname)
    if iface.node_id not in handles:
        return None
    unpinned = _unpin_access_neighbors(
        iface.node_id, iface.interface_name, iface.peer_node_id, iface.peer_interface_name, handles
    )
    if not unpinned:
        return None
    return _combine_proofs(
        "CROSS_NODE ground LinkDown verified",
        [kernel_verifier.verify_vxlan_absent(iface.vni), *unpinned],
    )


def _local_ground_delays(message) -> tuple[float, float]:
    """(station egress, satellite egress) delay of a LOCAL ground message, which names one end
    as node_id (its delay in latency_ms) and the other as its peer (peer_latency_ms). Each end's
    egress carries the direction that end transmits."""
    if message.node_id == message.gs_id:
        return message.latency_ms, message.peer_latency_ms
    if message.node_id == message.sat_id:
        return message.peer_latency_ms, message.latency_ms
    raise ValueError(
        f"LOCAL ground message names {message.node_id!r}, neither its station "
        f"{message.gs_id!r} nor its satellite {message.sat_id!r}"
    )


def _ground_terminal_rates(
    entry: node_agent_pb2.InterfaceUp | node_agent_pb2.KernelInventoryEntry,
) -> tuple[node_agent_pb2.TerminalRates, node_agent_pb2.TerminalRates]:
    """(ground station rates, satellite rates) of a LOCAL ground entry.

    ``rates`` belongs to the entry's own node and ``peer_rates`` to its peer.
    """
    if entry.node_id == entry.gs_id and entry.peer_node_id == entry.sat_id:
        return entry.rates, entry.peer_rates
    if entry.node_id == entry.sat_id and entry.peer_node_id == entry.gs_id:
        return entry.peer_rates, entry.rates
    raise ValueError(
        f"ground entry {entry.node_id}<->{entry.peer_node_id} does not name "
        f"{entry.gs_id} and {entry.sat_id}"
    )


def _isl_host_ifname(iface: node_agent_pb2.InterfaceUp) -> str:
    """The host-side veth that feeds a pod ISL interface."""
    return _end_host_ifname(iface)


def _ground_link_up(
    iface: node_agent_pb2.InterfaceUp, handles: dict[str, NamespaceHandle] | None = None
) -> EntryOutcome:
    """Bring up a ground link with bridge attach + shaping.

    Ground LinkUp sequence:
    1. attach_to_ground_bridge (host veths UP, sat gnd0 UP, mirred redirect)
       — carrier arrives on GS gnd0 automatically (LOWERLAYERDOWN → UP)
    2. Shape each end as its own terminal: pod egress at the terminal's
       transmit rate with the link delay, and the host-side veth feeding the
       pod at the terminal's receive rate

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.

    Layer 3 neighbor resolution (NDP) is NOT performed here. NDP is owned
    by FRR (IGP sessions, via IS-IS/OSPF hello) and the kernel NDP state
    machine (NodalPath sessions, on first forwarding attempt). See PRD
    §13.22.6 — v0.72 reversal.
    """
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pm = handles
        sat_pid = _require_pid(iface.sat_id, pm)
        gs_pid = _require_pid(iface.gs_id, pm)
        gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
        _admit_local_pass(iface.gs_id, gs_ifname, pm)
        _admit_local_pass(iface.sat_id, sat_ifname, pm)
        ground_bridge.attach_to_ground_bridge(
            iface.gs_id,
            iface.sat_id,
            sat_pid,
            gs_ifname=gs_ifname,
            sat_ifname=sat_ifname,
        )
        gs_rates, sat_rates = _ground_terminal_rates(iface)
        gs_delay_ms, sat_delay_ms = _local_ground_delays(iface)
        gs_port = ground_bridge._gs_host_veth(iface.gs_id, gs_ifname)
        sat_host = ground_bridge._sat_host_veth(iface.sat_id, sat_ifname)
        namespace_ops.apply_transmit_shaping(gs_pid, gs_ifname, gs_delay_ms, gs_rates.transmit_mbps)
        namespace_ops.apply_transmit_shaping(
            sat_pid, sat_ifname, sat_delay_ms, sat_rates.transmit_mbps
        )
        namespace_ops.apply_receive_shaping(gs_port, gs_rates.receive_mbps, access=True)
        namespace_ops.apply_receive_shaping(sat_host, sat_rates.receive_mbps, access=True)
        proofs = [
            kernel_verifier.verify_host_interface_state(gs_port, admin_up=True),
            kernel_verifier.verify_host_interface_state(sat_host, admin_up=True),
            kernel_verifier.verify_mirred(gs_port, sat_host),
            kernel_verifier.verify_mirred(sat_host, gs_port),
            kernel_verifier.verify_qdisc(
                gs_pid,
                gs_ifname,
                delay_ms=gs_delay_ms,
                transmit_mbps=gs_rates.transmit_mbps,
            ),
            kernel_verifier.verify_qdisc(
                sat_pid,
                sat_ifname,
                delay_ms=sat_delay_ms,
                transmit_mbps=sat_rates.transmit_mbps,
            ),
            kernel_verifier.verify_receive_shaping(gs_port, receive_mbps=gs_rates.receive_mbps),
            kernel_verifier.verify_receive_shaping(sat_host, receive_mbps=sat_rates.receive_mbps),
            *_pin_access_neighbors(iface.gs_id, gs_ifname, iface.sat_id, sat_ifname, pm),
            *_pin_access_neighbors(iface.sat_id, sat_ifname, iface.gs_id, gs_ifname, pm),
        ]
        # Each endpoint by its own manifest requirement.
        if _requires_mpls(iface.gs_id, pm):
            proofs.append(kernel_verifier.verify_mpls_input(gs_pid, gs_ifname))
        if _requires_mpls(iface.sat_id, pm):
            proofs.append(kernel_verifier.verify_mpls_input(sat_pid, sat_ifname))
        return _combine_proofs("LOCAL ground LinkUp verified", proofs)
    except Exception as exc:
        msg = f"Ground up failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def _update_long_delay_receive(entry: node_agent_pb2.LatencyEntry, pid: int) -> EntryOutcome:
    """A long-delay end's delay change: the received direction's delay, and the queue limit sized
    for it from the envelope, on its host-side veth. An update for a contact the end no longer
    carries changes nothing."""
    if not entry.contact_id or not entry.receive_latency_ms > 0:
        return _fail(
            node_agent_pb2.NODE_AGENT_INVALID_FIELD,
            f"long-delay SetLatency of {entry.node_id}/{entry.interface_name} needs its contact "
            "and the received delay",
        )
    host_ifname = namespace_ops.host_peer_of(pid, entry.interface_name)
    carried = namespace_ops.contact_of(host_ifname)
    if carried != entry.contact_id:
        return _ok(
            kernel_verifier.Proof.ok(
                f"{entry.node_id}/{entry.interface_name} carries contact {carried}, not "
                f"{entry.contact_id}: unchanged",
                f"host_ifname={host_ifname}",
            )
        )
    limit = _receive_limit(entry)
    plan = OperationPlan(
        operation_id=f"SetLatency:{entry.node_id}:{entry.interface_name}",
        operation_kind="SetLatency",
        target=f"{entry.node_id}/{entry.interface_name}",
        steps=(
            OperationStep(
                name="tc-netem-change-receive",
                action=lambda: namespace_ops.update_receive_delay(
                    host_ifname, entry.receive_latency_ms, limit
                ),
                verify=lambda: kernel_verifier.verify_receive_delay_queue(
                    host_ifname,
                    receive_mbps=entry.rates.receive_mbps,
                    delay_ms=entry.receive_latency_ms,
                    limit=limit,
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
    return _combine_proofs("long-delay SetLatency verified", result.proofs)


def _update_latency_entry(
    entry: node_agent_pb2.LatencyEntry, handles: dict[str, NamespaceHandle] | None = None
) -> EntryOutcome:
    """Update the netem delay and queue limit on a single interface.

    Uses tc "change" — does NOT touch admin state or re-add qdiscs.
    """
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pm = handles
        pid = _require_pid(entry.node_id, pm)
        if _long_delay_entry(entry, pm):
            return _update_long_delay_receive(entry, pid)
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
                        entry.rates.transmit_mbps,
                    ),
                    verify=lambda: kernel_verifier.verify_qdisc(
                        pid,
                        entry.interface_name,
                        delay_ms=entry.latency_ms,
                        transmit_mbps=entry.rates.transmit_mbps,
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
    handles: dict[str, NamespaceHandle] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.BatchLinkDownResponse:
    """Handle BatchLinkDown — per-interface locality."""
    start = _time.monotonic()
    outcomes: dict[tuple[str, str], EntryOutcome] = {}
    downed = 0
    stopped = 0

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
    if handles is None:
        raise ValueError("handles is None — wiring not complete?")
    pm = handles
    samples: dict[tuple[str, str], node_agent_pb2.LinkEndLoss] = {}
    for iface in request.interfaces:
        long_delay = _long_delay_entry(iface, pm)
        if long_delay:
            if not iface.contact_id:
                outcomes[_iface_key(iface)] = _fail(
                    node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                    f"long-delay LinkDown of {iface.node_id}/{iface.interface_name} names no "
                    "contact",
                )
                continue
            stage_op = (
                _stop_long_delay_admission
                if iface.stage == node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION
                else _close_long_delay_end
            )
            futures[_BATCH_POOL.submit(stage_op, iface, pm)] = iface
            continue
        if iface.stage == node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION:
            # A local link has no admission hook and no drain: it closes at once.
            outcomes[_iface_key(iface)] = _fail(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"{iface.node_id}/{iface.interface_name} is not a long-delay link end; it closes at once",
            )
            continue
        try:
            sample = _local_queue_sample(iface, pm)
        except Exception as exc:
            # The loss the close reports is part of its result: an end whose queues cannot be
            # read is not closed with an unknown loss.
            msg = (
                f"LinkDown {iface.node_id}/{iface.interface_name}: its queues could not be read "
                f"before the close: {exc}"
            )
            log.error(msg)
            outcomes[_iface_key(iface)] = _fail(node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED, msg)
            continue
        if sample is not None:
            samples[_iface_key(iface)] = sample
        if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE and iface.vni:
            if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
                # CROSS_NODE GROUND: detach VXLAN from existing host-side interface
                gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
                is_sat = iface.node_id == iface.sat_id
                if is_sat:
                    host_ifname = ground_bridge._sat_host_veth(iface.node_id, sat_ifname)
                    # Absent handle means the pod side lives on another host
                    # (legitimate cross-node state); a present handle must
                    # still verify before its namespace is mutated.
                    if iface.node_id in pm:
                        try:
                            sat_pid = _require_pid(iface.node_id, pm)
                        except PidNotFoundError as exc:
                            outcomes[_iface_key(iface)] = _fail(
                                node_agent_pb2.NODE_AGENT_PID_NOT_FOUND, str(exc)
                            )
                            continue
                    else:
                        sat_pid = 0
                else:
                    host_ifname = ground_bridge._gs_host_veth(iface.node_id, gs_ifname)
                    sat_pid = None
                fut = _BATCH_POOL.submit(
                    _cross_node_ground_down,
                    iface,
                    pm,
                    host_ifname,
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
            if outcome.success and _iface_key(iface) in samples:
                outcome = replace(outcome, loss=samples[_iface_key(iface)])
            if outcome.success and outcome.superseded:
                # A stage for a contact the end no longer carries changed nothing: no count, and
                # the substrate peer stays with the contact the end carries.
                pass
            elif outcome.success and iface.stage == node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION:
                stopped += 1
            elif outcome.success:
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
        outcomes.get(_iface_key(iface))
        or _fail(
            node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
            f"not attempted: batch aborted before {iface.node_id}/{iface.interface_name}",
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
        admissions_stopped=stopped,
        apply_time_ms=elapsed,
        interface_results=[
            _interface_result(iface, outcome)
            for iface, outcome in zip(request.interfaces, ordered_outcomes, strict=True)
        ],
    )


def _local_queue_sample(
    iface: node_agent_pb2.InterfaceDown, handles: dict[str, NamespaceHandle]
) -> node_agent_pb2.LinkEndLoss | None:
    """What a local link end's delay queues hold just before its LinkDown: a sample, not a
    count. A local link closes at once with no admission stop, so packets keep entering and
    leaving between this read and the reset; the result says so (``discarded_exact`` false).
    The queues are the transmit queues on the pod interfaces the entry closes; an end on another
    host is not sampled here."""
    if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
        gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
        ends = [(iface.gs_id, gs_ifname), (iface.sat_id, sat_ifname)]
    else:
        ends = [(iface.node_id, iface.interface_name)]
    held = 0
    dropped = 0
    sampled = False
    for node_id, ifname in ends:
        handle = handles.get(node_id)
        if handle is None:
            continue
        counts = loss.QueueDevice(ifname, handle.pid).counts()
        held += counts.held
        dropped += counts.dropped
        sampled = True
    if not sampled:
        return None
    return node_agent_pb2.LinkEndLoss(
        queue_dropped=dropped, discarded_at_close=held, discarded_exact=False
    )


def _receive_limit(entry) -> int:
    """The packet limit of a long-delay end's receive queue. The queue delays what the peer
    sends, so it is sized from the peer terminal's configured transmit rate (``peer_rates``) and
    the delay of the direction this end receives. Rejected alternative: this end's own receive
    rate, which sizes the queue for the wrong terminal when the two ends' rates differ."""
    return queue_limit_packets(
        transmit_mbps=entry.peer_rates.transmit_mbps, delay_ms=entry.receive_latency_ms
    )


def _prepare_long_delay_contact(
    pid: int, host_ifname: str, iface: node_agent_pb2.InterfaceUp
) -> tuple[list[kernel_verifier.Proof], node_agent_pb2.LinkEndLoss | None]:
    """PREPARE a contact on a long-delay end, with its admission stopped first: the transmitting
    pod interface's rate shaper and explicit leaf, the received direction's delay queue on the
    host-side veth (sized from the peer terminal's transmit rate, ``_receive_limit``), and the
    contact recorded on that veth.
    Nothing crosses the link until OPEN. Returns the proofs, and on a reopen what the contact
    that ended lost at this end.

    A repeated PREPARE of the contact the end carries changes and reports nothing. A contact that
    reopens while the last one drains keeps the drained packets in the same queue: they were
    sent to this same receiver. The ended contact's loss is what its admission filter took and
    what the queue dropped since the contact's last report; the new contact's tag starts from
    the counts as they stand. On a fresh end, refusals while the end carried no contact belong to
    no contact and are left out."""
    if not iface.contact_id:
        raise ValueError(
            f"long-delay LinkUp of {iface.node_id}/{iface.interface_name} names no contact"
        )
    if not iface.receive_latency_ms > 0:
        raise ValueError(
            f"long-delay LinkUp of {iface.node_id}/{iface.interface_name} has no received delay"
        )
    limit = _receive_limit(iface)
    admission.stop_admission(pid, iface.interface_name)
    taken = admission.read_admission(pid, iface.interface_name).taken
    if taken is None:
        raise RuntimeError(f"admission on {iface.interface_name} did not stop")
    tag = namespace_ops.contact_tag(host_ifname)
    namespace_ops.apply_long_delay_transmit_shaping(
        pid,
        iface.interface_name,
        iface.rates.transmit_mbps,
        access=iface.link_type == node_agent_pb2.LINK_TYPE_GROUND,
    )
    transmit_dropped = loss.QueueDevice(iface.interface_name, pid).counts().dropped
    ended: node_agent_pb2.LinkEndLoss | None = None
    if tag is not None and tag.contact_id != iface.contact_id:
        dropped = loss.QueueDevice(host_ifname, None).counts().dropped
        ended = node_agent_pb2.LinkEndLoss(
            admission_taken=_since_reported(taken, tag.taken_reported),
            queue_dropped=_since_reported(dropped, tag.dropped_reported),
            transmit_dropped=_since_reported(transmit_dropped, tag.transmit_dropped_reported),
            discarded_exact=True,
        )
    namespace_ops.apply_receive_delay_queue(
        host_ifname, iface.rates.receive_mbps, iface.receive_latency_ms, limit, fresh=tag is None
    )
    if tag is None or tag.contact_id != iface.contact_id:
        namespace_ops.tag_contact(
            host_ifname,
            namespace_ops.ContactTag(
                contact_id=iface.contact_id,
                peer=f"{iface.peer_node_id}/{iface.peer_interface_name}",
                taken_reported=taken,
                dropped_reported=loss.QueueDevice(host_ifname, None).counts().dropped,
                transmit_dropped_reported=transmit_dropped,
            ),
        )
    return [
        kernel_verifier.verify_pod_interface_exists(pid, iface.interface_name),
        admission.verify_admission_hook(pid, iface.interface_name, stopped=True),
        kernel_verifier.verify_long_delay_transmit(
            pid, iface.interface_name, transmit_mbps=iface.rates.transmit_mbps
        ),
        kernel_verifier.verify_receive_delay_queue(
            host_ifname,
            receive_mbps=iface.rates.receive_mbps,
            delay_ms=iface.receive_latency_ms,
            limit=limit,
        ),
        kernel_verifier.verify_contact(host_ifname, iface.contact_id),
    ], ended


def _open_prepared_contact(
    pid: int, host_ifname: str, iface: node_agent_pb2.InterfaceUp
) -> EntryOutcome:
    """OPEN a prepared long-delay contact: admit packets on the end. The end must carry the
    contact with its shaping and receive queue as PREPARE built them; anything else refuses, so a
    late OPEN never admits onto another contact. Reports what the stopped admission refused
    between PREPARE and OPEN. A repeated OPEN of an open contact changes and reports nothing."""
    tag = namespace_ops.contact_tag(host_ifname)
    if tag is None or tag.contact_id != iface.contact_id:
        return _fail(
            node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
            f"OPEN of {iface.node_id}/{iface.interface_name}: the end carries "
            f"{tag.contact_id if tag else 'no contact'}, not prepared contact {iface.contact_id}",
            dirty_kernel=True,
        )
    limit = _receive_limit(iface)
    prepared = [
        kernel_verifier.verify_long_delay_transmit(
            pid, iface.interface_name, transmit_mbps=iface.rates.transmit_mbps
        ),
        kernel_verifier.verify_receive_delay_queue(
            host_ifname,
            receive_mbps=iface.rates.receive_mbps,
            delay_ms=iface.receive_latency_ms,
            limit=limit,
        ),
    ]
    if not all(proof.verified for proof in prepared):
        return _combine_proofs("prepared contact not found for OPEN", prepared)
    # The admission filter's count is frozen first, so every packet it refused is reported, and
    # reported once: the tag records the count before the filter goes.
    frozen = admission.freeze_admission(pid, iface.interface_name)
    refused = 0
    if frozen is not None:
        refused = _since_reported(frozen, tag.taken_reported)
        namespace_ops.tag_contact(host_ifname, replace(tag, taken_reported=frozen))
        admission.open_admission(pid, iface.interface_name)
        # The filter and its count are gone; the next stop's filter counts from 0.
        namespace_ops.tag_contact(host_ifname, replace(tag, taken_reported=0))
    outcome = _combine_proofs(
        "long-delay contact open",
        [
            *prepared,
            admission.verify_admission_hook(pid, iface.interface_name, stopped=False),
            kernel_verifier.verify_contact(host_ifname, iface.contact_id),
        ],
    )
    return replace(
        outcome, loss=node_agent_pb2.LinkEndLoss(admission_taken=refused, discarded_exact=True)
    )


def _isl_link_up_carrier_stage(
    iface: node_agent_pb2.InterfaceUp, handles: dict[str, NamespaceHandle] | None = None
) -> EntryOutcome:
    """ISL link-up carrier stage: attach host-side + shaping. No NDP yet.

    The pod interface's egress carries its terminal's transmit rate and the
    link delay; the host-side veth that feeds it carries the terminal's
    receive rate.

    LOCAL ISLs: pod-side interface is already admin UP (from wiring). Attaches
    host-side veths (UP + tc mirred) → carrier appears on pod-side.

    CROSS_NODE ISLs: VXLAN tunnel + veth already created in the preparation stage and
    interface is UP. Only tc shaping needs to be applied.

    Returns error string or None.
    """
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pid = _require_pid(iface.node_id, handles)
        long_delay = handles[iface.node_id].is_long_delay(iface.interface_name)
        if long_delay and iface.stage == node_agent_pb2.LINK_UP_STAGE_OPEN:
            return _open_prepared_contact(pid, _isl_host_ifname(iface), iface)
        if long_delay:
            # Admission stops before the host side can forward: nothing crosses until OPEN.
            admission.stop_admission(pid, iface.interface_name)
        if iface.locality == node_agent_pb2.LOCALITY_LOCAL:
            ground_bridge.attach_isl(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
            )
        host_ifname = _isl_host_ifname(iface)
        ended: node_agent_pb2.LinkEndLoss | None = None
        if long_delay:
            proofs, ended = _prepare_long_delay_contact(pid, host_ifname, iface)
        else:
            namespace_ops.apply_transmit_shaping(
                pid, iface.interface_name, iface.latency_ms, iface.rates.transmit_mbps
            )
            namespace_ops.apply_receive_shaping(host_ifname, iface.rates.receive_mbps)
            proofs = [
                kernel_verifier.verify_pod_interface_exists(pid, iface.interface_name),
                kernel_verifier.verify_qdisc(
                    pid,
                    iface.interface_name,
                    delay_ms=iface.latency_ms,
                    transmit_mbps=iface.rates.transmit_mbps,
                ),
                kernel_verifier.verify_receive_shaping(
                    host_ifname, receive_mbps=iface.rates.receive_mbps
                ),
            ]
        # A long-delay link carries static neighbors: address resolution cannot complete across
        # it. Installed at every LinkUp, since a cross-host link's pod end is new each time.
        neighbors = handles[iface.node_id].static_neighbors_on(iface.interface_name)
        if neighbors is not None:
            namespace_ops.install_static_neighbors(
                pid, iface.interface_name, neighbors.lladdr, neighbors.addresses
            )
        if _requires_mpls(iface.node_id, handles):
            proofs.append(kernel_verifier.verify_mpls_input(pid, iface.interface_name))
        if neighbors is not None:
            proofs.extend(
                [
                    # This end's own address, the one the far end pinned; the far end proves
                    # its own the same way.
                    kernel_verifier.verify_link_mac(
                        pid,
                        iface.interface_name,
                        link_interface_mac(iface.node_id, iface.interface_name),
                    ),
                    kernel_verifier.verify_static_neighbors(
                        pid, iface.interface_name, neighbors.lladdr, neighbors.addresses
                    ),
                ]
            )
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
        outcome = _combine_proofs("ISL LinkUp verified", proofs)
        if ended is not None:
            # The end reopened: what the contact that ended lost here (its queue carries on
            # into this one, nothing discarded).
            outcome = replace(outcome, loss=ended)
        return outcome
    except Exception as exc:
        msg = f"ISL up carrier stage failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED, msg, dirty_kernel=True)


def handle_batch_link_up(
    request: node_agent_pb2.BatchLinkUpRequest,
    context=None,
    handles: dict[str, NamespaceHandle] | None = None,
    fence: RuntimeFence | None = None,
) -> node_agent_pb2.BatchLinkUpResponse:
    """Handle BatchLinkUp command.

    Preparation stage (sequential): create VXLAN tunnels for CROSS_NODE interfaces.
    Carrier stage (concurrent): bring host-side veth carrier UP, shape each
      pod interface's egress at its terminal's transmit rate with the link's
      netem delay, and shape the host-side veth that feeds it at the
      terminal's receive rate. ACK as soon as this stage
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

    if handles is None:
        raise ValueError("handles is None — wiring not complete?")
    pm = handles

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

    # A long-delay ISL end opens in two stages, PREPARE then OPEN; every other end opens at once
    # (OPEN). A wrong stage is refused before any change.
    stage_refused: set[tuple[str, str]] = set()
    for iface in request.interfaces:
        long_delay = _long_delay_entry(iface, pm)
        if not long_delay and iface.stage == node_agent_pb2.LINK_UP_STAGE_PREPARE:
            outcomes[_iface_key(iface)] = _fail(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"{iface.node_id}/{iface.interface_name} is not a long-delay link end; it opens at once",
            )
            stage_refused.add(_iface_key(iface))

    # Preparation stage: Create VXLAN tunnels for CROSS_NODE interfaces.
    # Each interface carries its own locality — only CROSS_NODE interfaces
    # need VXLAN. LOCAL interfaces in the same batch are unaffected. A long-delay end's tunnel
    # is created at PREPARE; its OPEN finds the prepared one.
    #
    # ISL CROSS_NODE: create VXLAN + veth pair, move veth into pod namespace
    # GROUND CROSS_NODE: create VXLAN in host ns, tc mirred to existing host-side
    #   interface (satellite _gnd_{sat} or GS _gbr-{gs}). Pod-side gnd0 already
    #   exists from wiring. Same carrier model as LOCAL ground links.
    local_ip: str | None = None
    preparation_failed: set[tuple[str, str]] = set(stage_refused)
    for iface in request.interfaces:
        if iface.locality != node_agent_pb2.LOCALITY_CROSS_NODE or _iface_key(iface) in (
            stage_refused
        ):
            continue
        if (
            iface.link_type == node_agent_pb2.LINK_TYPE_ISL
            and iface.stage == node_agent_pb2.LINK_UP_STAGE_OPEN
            and pm[iface.node_id].is_long_delay(iface.interface_name)
        ):
            continue
        if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND and _long_delay_entry(iface, pm):
            # A long-delay ground end joins its tunnel in its own PREPARE (carrier stage).
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
                _admit_local_pass(iface.node_id, iface.interface_name, pm)
                vxlan.attach_cross_node_ground(
                    local_host_ifname=host_ifname,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                    sat_pid=sat_pid if is_sat else None,
                    sat_ifname=sat_ifname,
                )
                namespace_ops.apply_transmit_shaping(
                    local_pid,
                    iface.interface_name,
                    iface.latency_ms,
                    iface.rates.transmit_mbps,
                )
                namespace_ops.apply_receive_shaping(
                    host_ifname, iface.rates.receive_mbps, access=True
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
                vxlan_if = vxlan_host_ifnames(iface.vni).tunnel
                ground_proofs = [
                    kernel_verifier.verify_vxlan(
                        iface.vni, local_ip=local_ip, remote_ip=iface.remote_node_ip
                    ),
                    kernel_verifier.verify_mirred(vxlan_if, host_ifname),
                    kernel_verifier.verify_mirred(host_ifname, vxlan_if),
                    kernel_verifier.verify_qdisc(
                        local_pid,
                        iface.interface_name,
                        delay_ms=iface.latency_ms,
                        transmit_mbps=iface.rates.transmit_mbps,
                    ),
                    kernel_verifier.verify_receive_shaping(
                        host_ifname, receive_mbps=iface.rates.receive_mbps
                    ),
                ]
                if _requires_mpls(iface.node_id, pm):
                    ground_proofs.append(
                        kernel_verifier.verify_mpls_input(local_pid, iface.interface_name)
                    )
                ground_proofs.extend(
                    _pin_access_neighbors(
                        iface.node_id,
                        iface.interface_name,
                        iface.peer_node_id,
                        iface.peer_interface_name,
                        pm,
                    )
                )
                outcomes[_iface_key(iface)] = _combine_proofs(
                    "CROSS_NODE ground LinkUp verified", ground_proofs
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
                created = vxlan.create_vxlan_link(
                    pid=pid,
                    node_id=iface.node_id,
                    ifname=iface.interface_name,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                    long_delay=pm[iface.node_id].is_long_delay(iface.interface_name),
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
                if _requires_mpls(iface.node_id, pm):
                    # The creating operation configures what it created and
                    # only verifies what it reused; a mismatch on reuse is a
                    # refusal. The read-back reaches the reply in the carrier
                    # stage's proofs.
                    configure_mpls_input(
                        pid,
                        iface.interface_name,
                        created=created,
                        subject=f"VNI {iface.vni} {iface.node_id}/{iface.interface_name}",
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
        if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND and _long_delay_entry(iface, pm):
            carrier_futures[_BATCH_POOL.submit(_ground_long_delay_stage, iface, pm, fence)] = iface
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
        outcomes.get(_iface_key(iface))
        or _fail(
            node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
            f"not attempted: batch aborted before {iface.node_id}/{iface.interface_name}",
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
        carried_contact_id=outcome.survey[0] if outcome.survey else "",
        admission_stopped=outcome.survey[1] if outcome.survey else False,
        receive_latency_ms=outcome.survey[2] if outcome.survey else 0.0,
        carried_peer=outcome.survey[3] if outcome.survey else "",
    )


def _survey_long_delay_end(
    entry: node_agent_pb2.KernelInventoryEntry,
    handles: dict[str, NamespaceHandle] | None = None,
) -> EntryOutcome:
    """Read-only report of what a long-delay ISL end carries: its contact, whether its admission
    is stopped, and its receive queue's delay. A restarted Scheduler adopts it before it
    dispatches. A cross-node end whose pod interface is gone carries nothing."""
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pid = _require_pid(entry.node_id, handles)
        # A ground terminal is surveyed for whatever pass it carries: its node must have a
        # long-delay access peer, whichever peer the pass links it to.
        surveyable = (
            handles[entry.node_id].carries_long_delay_access
            if entry.link_type == node_agent_pb2.LINK_TYPE_GROUND
            else _long_delay_entry(entry, handles)
        )
        if not surveyable:
            return _fail(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"{entry.node_id}/{entry.interface_name} is not a long-delay end",
            )
        host_ifname = _end_host_ifname(entry)
        tag = namespace_ops.contact_tag(host_ifname)
        carried = tag.contact_id if tag is not None else ""
        stopped = False
        if carried:
            stopped = admission.read_admission(pid, entry.interface_name).stopped
        delay_ms = kernel_verifier.receive_queue_delay_ms(host_ifname) if carried else 0.0
        return replace(
            _ok(
                kernel_verifier.Proof.ok(
                    f"{entry.node_id}/{entry.interface_name} surveyed",
                    f"host_ifname={host_ifname}",
                    f"contact={carried or None}",
                    f"admission_stopped={stopped}",
                    f"receive_delay_ms={delay_ms}",
                )
            ),
            survey=(carried, stopped, delay_ms, tag.peer if tag is not None else ""),
        )
    except Exception as exc:
        msg = f"long-delay survey failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED, msg)


def _verify_long_delay_inventory_entry(
    entry: node_agent_pb2.KernelInventoryEntry,
    handles: dict[str, NamespaceHandle] | None = None,
) -> EntryOutcome:
    """Read-only proof of a long-delay end, fixed or ground, in the stage its contact is in: open
    or draining (the contact on the host-side veth, admission open or stopped, the rate shaper,
    the received direction's delay queue), or closed (the end carries no contact)."""
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pid = _require_pid(entry.node_id, handles)
        if not _long_delay_entry(entry, handles):
            return _fail(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"{entry.node_id}/{entry.interface_name} is not a long-delay end",
            )
        host_ifname = _end_host_ifname(entry)
        if not entry.expected_admin_up:
            carried = namespace_ops.contact_of(host_ifname)
            if carried == entry.contact_id:
                return _fail(
                    node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
                    f"{entry.node_id}/{entry.interface_name} still carries closed contact "
                    f"{entry.contact_id}",
                    dirty_kernel=True,
                )
            return _ok(
                kernel_verifier.Proof.ok(
                    f"{entry.node_id}/{entry.interface_name} closed",
                    f"host_ifname={host_ifname}",
                    f"contact={carried}",
                )
            )
        limit = _receive_limit(entry)
        return _combine_proofs(
            "long-delay end verified",
            [
                kernel_verifier.verify_contact(host_ifname, entry.contact_id),
                admission.verify_admission_hook(
                    pid, entry.interface_name, stopped=entry.expected_admission_stopped
                ),
                kernel_verifier.verify_long_delay_transmit(
                    pid,
                    entry.interface_name,
                    transmit_mbps=entry.rates.transmit_mbps,
                    # A stop purged the leaf; an open or prepared contact holds it.
                    leaf=not entry.expected_admission_stopped,
                ),
                kernel_verifier.verify_receive_delay_queue(
                    host_ifname,
                    receive_mbps=entry.rates.receive_mbps,
                    delay_ms=entry.receive_latency_ms,
                    limit=limit,
                ),
            ],
        )
    except Exception as exc:
        msg = f"long-delay inventory failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED, msg, dirty_kernel=True)


def _verify_kernel_inventory_entry(
    entry: node_agent_pb2.KernelInventoryEntry,
    handles: dict[str, NamespaceHandle] | None = None,
) -> EntryOutcome:
    """Read-only GS-facing verification for actuation recovery."""
    try:
        if handles is None:
            raise ValueError("handles is None — wiring not complete?")
        pm = handles
        gs_ifname, sat_ifname = _extract_ground_ifaces(entry)
        is_sat = entry.node_id == entry.sat_id
        local_pid = _require_pid(entry.node_id, pm)
        host_ifname = (
            ground_bridge._sat_host_veth(entry.sat_id, sat_ifname)
            if is_sat
            else ground_bridge._gs_host_veth(entry.gs_id, gs_ifname)
        )
        local_ifname = sat_ifname if is_sat else gs_ifname

        def _mpls_proofs() -> list[kernel_verifier.Proof]:
            """Each endpoint's MPLS input by its own manifest requirement; the pod
            interfaces exist in both expected states."""
            found: list[kernel_verifier.Proof] = []
            if entry.locality == node_agent_pb2.LOCALITY_LOCAL:
                if _requires_mpls(entry.gs_id, pm):
                    found.append(
                        kernel_verifier.verify_mpls_input(_require_pid(entry.gs_id, pm), gs_ifname)
                    )
                if _requires_mpls(entry.sat_id, pm):
                    found.append(
                        kernel_verifier.verify_mpls_input(
                            _require_pid(entry.sat_id, pm), sat_ifname
                        )
                    )
            elif _requires_mpls(entry.node_id, pm):
                found.append(kernel_verifier.verify_mpls_input(local_pid, local_ifname))
            return found

        if entry.expected_admin_up:
            proofs = [
                kernel_verifier.verify_host_interface_state(host_ifname, admin_up=True),
            ]
            if entry.locality == node_agent_pb2.LOCALITY_LOCAL:
                # LOCAL ground links are verified on the one agent that owns
                # both pod namespaces and both host-side veths; each end is
                # proven against its own terminal's rates.
                gs_rates, sat_rates = _ground_terminal_rates(entry)
                gs_delay_ms, sat_delay_ms = _local_ground_delays(entry)
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
                            delay_ms=gs_delay_ms,
                            transmit_mbps=gs_rates.transmit_mbps,
                        ),
                        kernel_verifier.verify_qdisc(
                            sat_pid,
                            sat_ifname,
                            delay_ms=sat_delay_ms,
                            transmit_mbps=sat_rates.transmit_mbps,
                        ),
                        kernel_verifier.verify_receive_shaping(
                            gs_port, receive_mbps=gs_rates.receive_mbps
                        ),
                        kernel_verifier.verify_receive_shaping(
                            sat_host, receive_mbps=sat_rates.receive_mbps
                        ),
                    ]
                )
            else:
                proofs.extend(
                    [
                        kernel_verifier.verify_qdisc(
                            local_pid,
                            local_ifname,
                            delay_ms=entry.latency_ms,
                            transmit_mbps=entry.rates.transmit_mbps,
                        ),
                        kernel_verifier.verify_receive_shaping(
                            host_ifname, receive_mbps=entry.rates.receive_mbps
                        ),
                    ]
                )
                if entry.vni:
                    local_ip = _discover_local_ip()
                    vxlan_if = vxlan_host_ifnames(entry.vni).tunnel
                    proofs.extend(
                        [
                            kernel_verifier.verify_vxlan(
                                entry.vni, local_ip=local_ip, remote_ip=entry.remote_node_ip
                            ),
                            kernel_verifier.verify_mirred(host_ifname, vxlan_if),
                            kernel_verifier.verify_mirred(vxlan_if, host_ifname),
                        ]
                    )
            proofs.extend(_mpls_proofs())
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
        proofs.extend(_mpls_proofs())
        return _combine_proofs("KernelInventory expected-down verified", proofs)
    except Exception as exc:
        msg = f"KernelInventory failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return _fail(node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED, msg, dirty_kernel=True)


def handle_kernel_inventory(
    request: node_agent_pb2.KernelInventoryRequest,
    context=None,
    handles: dict[str, NamespaceHandle] | None = None,
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

    if handles is None:
        raise ValueError("handles is None — wiring not complete?")

    # A request naming no station proves long-delay ends, fixed or ground, as the contract reads
    # it (``validate_kernel_inventory_request``); a station-scoped request proves the station's
    # local passes. Rejected alternative: choosing the proof by link type, which proved a
    # long-delay ground end against the local ground layout and found no pod-interface queue.
    def prover(entry: node_agent_pb2.KernelInventoryEntry):
        if entry.survey:
            return _survey_long_delay_end
        if not request.gs_id:
            return _verify_long_delay_inventory_entry
        return _verify_kernel_inventory_entry

    futures = {
        _BATCH_POOL.submit(prover(entry), entry, handles): entry for entry in request.entries
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
        outcomes.get(_iface_key(entry))
        or _fail(
            node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
            f"not attempted: batch aborted before {entry.node_id}/{entry.interface_name}",
            dirty_kernel=True,
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
    handles: dict[str, NamespaceHandle] | None = None,
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
        outcome = _update_latency_entry(entry, handles)
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
