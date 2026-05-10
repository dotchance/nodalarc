# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent RPC handler implementations.

Executes kernel operations dispatched by scheduler/dispatcher.py via
NATS request/reply. Uses namespace_ops.py and ground_bridge.py for
all netlink operations (setns-based, no fork).

IMPORTANT — node ID case sensitivity:
  Node IDs in gRPC messages MUST use the canonical case from the
  AddressingScheme (e.g., "sat-P01S02" not "sat-p01s02"). The ground
  bridge naming helpers derive host veth names from the node ID
  (e.g., "_gnd_P01S02"), and Linux interface names are case-sensitive.
  The Scheduler derives canonical node IDs from (plane, slot) via the
  AddressingScheme, so this should be correct automatically.

Error handling: every per-link operation is wrapped in try/except.
A single failing link does not prevent other links in the batch from
being processed. Failures are logged with full context and returned
in the gRPC response error field.
"""

from __future__ import annotations

import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed

from nodalarc.proto import node_agent_pb2

from node_agent import ground_bridge, namespace_ops, vxlan

log = logging.getLogger(__name__)

# Thread pool for concurrent batch execution within a single RPC.
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
        log.warning("HOST_IP env var not set — VXLAN tunnels will use wrong IP")
        import socket

        _local_ip = socket.gethostbyname(socket.gethostname())
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


def _interface_result(iface, error: str | None) -> node_agent_pb2.InterfaceResult:
    return node_agent_pb2.InterfaceResult(
        node_id=iface.node_id,
        interface_name=iface.interface_name,
        success=error is None,
        error_message=error or "",
    )


# ---------------------------------------------------------------------------
# Per-link operation functions (called concurrently within batches)
# ---------------------------------------------------------------------------


def _isl_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Deactivate an ISL link. Returns error string or None.

    LOCAL ISLs: detaches host-side veths (brings host-side DOWN → carrier
    drops on pod-side). Pod-side interface remains admin UP in LOWERLAYERDOWN
    state. Pod-side tc qdiscs are left in place — apply_link_shaping uses
    replace semantics on the next LinkUp, so orphaned qdiscs are harmless.

    CROSS_NODE ISLs: destroys the VXLAN tunnel (handled separately in the
    batch handler's Phase 0 teardown).
    """
    try:
        if iface.locality == node_agent_pb2.LOCAL:
            ground_bridge.detach_isl(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
            )
        return None
    except Exception as exc:
        msg = f"ISL down failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def _ground_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Tear down a ground link. Returns error string or None.

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
        return None
    except Exception as exc:
        msg = f"Ground down failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return msg


def _ground_link_up(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> str | None:
    """Bring up a ground link with bridge attach + shaping. Returns error or None.

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
        return None
    except Exception as exc:
        msg = f"Ground up failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return msg


def _update_latency_entry(
    entry: node_agent_pb2.LatencyEntry, pid_map: dict[str, int] | None = None
) -> str | None:
    """Update netem delay on a single interface. Returns error or None.

    Uses tc "change" — does NOT touch admin state or re-add qdiscs.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pm = pid_map
        pid = _require_pid(entry.node_id, pm)
        namespace_ops.update_delay(pid, entry.interface_name, entry.latency_ms)
        return None
    except Exception as exc:
        msg = f"Latency update failed {entry.node_id}/{entry.interface_name}: {exc}"
        log.warning(msg)
        return msg


# ---------------------------------------------------------------------------
# RPC handler functions (called from server.py servicer)
# ---------------------------------------------------------------------------


def handle_batch_link_down(
    request: node_agent_pb2.BatchLinkDownRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.BatchLinkDownResponse:
    """Handle BatchLinkDown — per-interface locality."""
    start = _time.monotonic()
    errors: list[str] = []
    interface_errors: dict[tuple[str, str], str | None] = {}
    downed = 0

    # Submit all operations concurrently — each interface carries its own locality
    futures = {}
    if pid_map is None:
        raise ValueError("pid_map is None — wiring not complete?")
    pm = pid_map
    for iface in request.interfaces:
        if iface.locality == node_agent_pb2.CROSS_NODE and iface.vni:
            if iface.link_type == node_agent_pb2.GROUND:
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
                pid = pm.get(iface.node_id, 0)
                fut = _BATCH_POOL.submit(
                    vxlan.destroy_vxlan_link, pid, iface.interface_name, iface.vni
                )
        elif iface.link_type == node_agent_pb2.GROUND:
            fut = _BATCH_POOL.submit(_ground_link_down, iface, pm)
        else:
            fut = _BATCH_POOL.submit(_isl_link_down, iface, pm)
        futures[fut] = iface

    # Collect results — wait for ALL before returning ACK
    from node_agent import substrate_monitor

    for fut in as_completed(futures):
        iface = futures[fut]
        try:
            err = fut.result(timeout=10)
            if err is None:
                downed += 1
                interface_errors[_iface_key(iface)] = None
                # Track VXLAN peer removal for substrate measurement
                if iface.locality == node_agent_pb2.CROSS_NODE and iface.remote_node_ip:
                    substrate_monitor.remove_peer(iface.remote_node_ip)
            else:
                interface_errors[_iface_key(iface)] = err
                errors.append(err)
        except Exception as exc:
            err = f"Unexpected error for {iface.node_id}/{iface.interface_name}: {exc}"
            interface_errors[_iface_key(iface)] = err
            errors.append(err)

    elapsed = (_time.monotonic() - start) * 1000
    error_msg = "; ".join(errors) if errors else ""
    if errors:
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

    return node_agent_pb2.BatchLinkDownResponse(
        success=not errors,
        error_message=error_msg,
        interfaces_downed=downed,
        apply_time_ms=elapsed,
        interface_results=[
            _interface_result(iface, interface_errors.get(_iface_key(iface), "not attempted"))
            for iface in request.interfaces
        ],
    )


def _isl_link_up_phase1(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> str | None:
    """Phase 1 of ISL link-up: attach host-side + shaping. No NDP yet.

    LOCAL ISLs: pod-side interface is already admin UP (from wiring). Attaches
    host-side veths (UP + tc mirred) → carrier appears on pod-side.

    CROSS_NODE ISLs: VXLAN tunnel + veth already created in Phase 0 and
    interface is UP. Only tc shaping needs to be applied.

    Returns error string or None.
    """
    try:
        if pid_map is None:
            raise ValueError("pid_map is None — wiring not complete?")
        pid = _require_pid(iface.node_id, pid_map)
        if iface.locality == node_agent_pb2.LOCAL:
            ground_bridge.attach_isl(
                iface.node_id,
                iface.interface_name,
                iface.peer_node_id,
                iface.peer_interface_name,
            )
        namespace_ops.apply_link_shaping(
            pid, iface.interface_name, iface.latency_ms, iface.bandwidth_mbps
        )
        return None
    except Exception as exc:
        msg = f"ISL up phase1 failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def handle_batch_link_up(
    request: node_agent_pb2.BatchLinkUpRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.BatchLinkUpResponse:
    """Handle BatchLinkUp RPC.

    Phase 0 (sequential): Create VXLAN tunnels for CROSS_NODE interfaces.
    Phase 1 (concurrent): Bring host-side veth carrier UP and apply
      tc tbf + tc netem shaping on every interface. ACK as soon as Phase 1
      is complete — Layer 2 carrier transitions are the Node Agent's
      contract with the Scheduler.

    Layer 3 neighbor resolution (NDP) is NOT performed. Per PRD §13.22.6
    (v0.72 reversal), NDP is owned by FRR (IGP sessions — IS-IS/OSPF hello
    fires NS naturally on carrier-up) and the kernel NDP state machine
    (NodalPath sessions — resolves on first forwarding attempt). The
    previous `_isl_ndp_phase2` nsenter-ping storm and its `trigger_ndp_and_wait`
    polling loop are deleted: a Layer 2 substrate actuator must not do
    Layer 3 work, the subprocess.run-per-link pattern did not scale, and
    the per-attempt `_ns_lock` contention defeated the entire simultaneity
    goal.
    """
    start = _time.monotonic()
    errors: list[str] = []
    interface_errors: dict[tuple[str, str], str | None] = {}
    upped = 0
    if pid_map is None:
        raise ValueError("pid_map is None — wiring not complete?")
    pm = pid_map

    # Validate PIDs. Per-interface locality:
    # LOCAL GROUND needs both gs_id and sat_id PIDs (bridge ops).
    # CROSS_NODE needs only node_id PID (VXLAN into local pod).
    # LOCAL ISL needs node_id PID.
    missing = []
    for iface in request.interfaces:
        if iface.link_type == node_agent_pb2.GROUND and iface.locality == node_agent_pb2.LOCAL:
            if pm.get(iface.gs_id, 0) == 0:
                missing.append(iface.gs_id)
            if pm.get(iface.sat_id, 0) == 0:
                missing.append(iface.sat_id)
        else:
            if pm.get(iface.node_id, 0) == 0:
                missing.append(iface.node_id)
    if missing:
        unique = sorted(set(missing))
        msg = f"PID not found for {len(unique)} node(s): {', '.join(unique[:10])}"
        if len(unique) > 10:
            msg += f" ... and {len(unique) - 10} more"
        if not pm:
            # pid_map is empty — wiring in progress, not a real error
            log.info("BatchLinkUp %s deferred: wiring in progress, pid_map empty", request.batch_id)
        else:
            log.error("BatchLinkUp %s REJECTED: %s", request.batch_id, msg)
        return node_agent_pb2.BatchLinkUpResponse(
            success=False,
            error_message=msg,
            interfaces_upped=0,
            apply_time_ms=0,
            interface_results=[_interface_result(iface, msg) for iface in request.interfaces],
        )

    # Phase 0: Create VXLAN tunnels for CROSS_NODE interfaces.
    # Each interface carries its own locality — only CROSS_NODE interfaces
    # need VXLAN. LOCAL interfaces in the same batch are unaffected.
    #
    # ISL CROSS_NODE: create VXLAN + veth pair, move veth into pod namespace
    # GROUND CROSS_NODE: create VXLAN in host ns, tc mirred to existing host-side
    #   interface (satellite _gnd_{sat} or GS _gbr-{gs}). Pod-side gnd0 already
    #   exists from wiring. Same carrier model as LOCAL ground links.
    local_ip: str | None = None
    cross_node_ground: list[node_agent_pb2.InterfaceUp] = []
    phase0_failed: set[tuple[str, str]] = set()
    for iface in request.interfaces:
        if iface.locality != node_agent_pb2.CROSS_NODE:
            continue
        if not iface.remote_node_ip or not iface.vni:
            link_desc = "GROUND" if iface.link_type == node_agent_pb2.GROUND else "ISL"
            err = (
                f"CROSS_NODE {link_desc} {iface.node_id}/{iface.interface_name}: "
                f"missing remote_node_ip or vni"
            )
            errors.append(err)
            interface_errors[_iface_key(iface)] = err
            phase0_failed.add(_iface_key(iface))
            continue
        if local_ip is None:
            local_ip = _discover_local_ip()

        if iface.link_type == node_agent_pb2.GROUND:
            # GROUND: attach via existing host-side infrastructure
            try:
                gs_ifname, sat_ifname = _extract_ground_ifaces(iface)
                is_sat = iface.node_id == iface.sat_id
                if is_sat:
                    host_ifname = ground_bridge._sat_host_veth(iface.node_id, sat_ifname)
                    sat_pid = pm.get(iface.node_id, 0)
                else:
                    host_ifname = ground_bridge._gs_host_veth(iface.node_id, gs_ifname)
                    sat_pid = None
                vxlan.attach_cross_node_ground(
                    local_host_ifname=host_ifname,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                    sat_pid=sat_pid if is_sat else None,
                    sat_ifname=sat_ifname,
                )
                from node_agent import substrate_monitor

                substrate_monitor.add_peer(iface.remote_node_ip)
                cross_node_ground.append(iface)
            except Exception as exc:
                err = f"VXLAN ground attach failed {iface.node_id}/{iface.interface_name}: {exc}"
                errors.append(err)
                interface_errors[_iface_key(iface)] = err
                phase0_failed.add(_iface_key(iface))
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
                from node_agent import substrate_monitor

                substrate_monitor.add_peer(iface.remote_node_ip)
            except Exception as exc:
                err = f"VXLAN create failed {iface.node_id}/{iface.interface_name}: {exc}"
                errors.append(err)
                interface_errors[_iface_key(iface)] = err
                phase0_failed.add(_iface_key(iface))

    # Phase 1: Bring interfaces UP + apply shaping.
    # - LOCAL GROUND: bridge + mirred attach (_ground_link_up)
    # - CROSS_NODE GROUND: fully handled in Phase 0 — skip
    # - LOCAL/CROSS_NODE ISL: UP + shaping (_isl_link_up_phase1)
    phase1_futures = {}
    for iface in request.interfaces:
        if _iface_key(iface) in phase0_failed:
            continue
        if iface in cross_node_ground:
            interface_errors[_iface_key(iface)] = None
            continue  # Fully handled in Phase 0
        if iface.link_type == node_agent_pb2.GROUND and iface.locality == node_agent_pb2.LOCAL:
            phase1_futures[_BATCH_POOL.submit(_ground_link_up, iface, pm)] = iface
        else:
            phase1_futures[_BATCH_POOL.submit(_isl_link_up_phase1, iface, pm)] = iface

    # Wait for ALL phase 1 to complete. ACK on completion — Layer 3
    # neighbor resolution happens asynchronously in FRR / kernel NDP
    # (see docstring and PRD §13.22.6).
    for fut in as_completed(phase1_futures):
        iface = phase1_futures[fut]
        try:
            err = fut.result(timeout=10)
            if err is None:
                upped += 1
                interface_errors[_iface_key(iface)] = None
            else:
                interface_errors[_iface_key(iface)] = err
                errors.append(err)
        except Exception as exc:
            err = f"Phase1 error for {iface.node_id}/{iface.interface_name}: {exc}"
            interface_errors[_iface_key(iface)] = err
            errors.append(err)

    # Count cross-node ground links handled in Phase 0
    upped += len(cross_node_ground)

    elapsed = (_time.monotonic() - start) * 1000
    error_msg = "; ".join(errors) if errors else ""
    if errors:
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

    return node_agent_pb2.BatchLinkUpResponse(
        success=not errors,
        error_message=error_msg,
        interfaces_upped=upped,
        apply_time_ms=elapsed,
        interface_results=[
            _interface_result(iface, interface_errors.get(_iface_key(iface), "not attempted"))
            for iface in request.interfaces
        ],
    )


def handle_set_latency(
    request: node_agent_pb2.SetLatencyRequest,
    context=None,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.SetLatencyResponse:
    """Handle SetLatency RPC.

    Updates tc netem delay on existing qdisc chains. Does NOT change
    admin state, re-add qdiscs, or touch anything other than the netem
    delay parameter.
    """
    errors: list[str] = []
    updated = 0

    for entry in request.entries:
        err = _update_latency_entry(entry, pid_map)
        if err is None:
            updated += 1
        else:
            errors.append(err)

    error_msg = "; ".join(errors) if errors else ""
    return node_agent_pb2.SetLatencyResponse(
        success=not errors,
        error_message=error_msg,
        entries_updated=updated,
    )
