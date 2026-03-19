"""Node Agent RPC handler implementations.

Each handler maps directly to the dispatch logic in
orchestrator/realtime_dispatcher.py, calling the same netlink functions
from namespace_ops.py and ground_bridge.py (copied from link_manager.py).

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

import grpc

from node_agent import ground_bridge, namespace_ops
from node_agent.proto import node_agent_pb2

log = logging.getLogger(__name__)

# Thread pool for concurrent batch execution within a single RPC.
# Bounded to avoid resource exhaustion on large batches.
_BATCH_POOL = ThreadPoolExecutor(max_workers=8)


# ---------------------------------------------------------------------------
# PID resolution — containerized Scheduler sends pid=0, Node Agent resolves
# ---------------------------------------------------------------------------


def _resolve_pid(pid: int, node_id: str, pid_map: dict[str, int]) -> int:
    """Resolve PID from local map if the Scheduler sent pid=0."""
    if pid > 0:
        return pid
    resolved = pid_map.get(node_id, 0)
    if resolved == 0:
        log.warning("Cannot resolve PID for %s (not in local pid_map)", node_id)
    return resolved


# ---------------------------------------------------------------------------
# Per-link operation functions (called concurrently within batches)
# ---------------------------------------------------------------------------


def _isl_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Admin-down a single ISL interface. Returns error string or None."""
    try:
        pid = _resolve_pid(iface.pid, iface.node_id, pid_map or {})
        namespace_ops.set_interface_down(pid, iface.interface_name)
        return None
    except Exception as exc:
        msg = f"ISL down failed ns({iface.pid})/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def _ground_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Tear down a ground link. Returns error string or None.

    Sequence (matches realtime_dispatcher.py L383-390):
    1. Remove tc shaping (netem/tbf) from satellite gnd0
    2. Remove tc shaping (netem/tbf) from GS gnd0
    3. Detach via ground_bridge.detach_from_ground_bridge which:
       a. Remove tc mirred redirect from GS host veth
       b. Remove tc mirred redirect from sat host veth
       c. Bring satellite gnd0 DOWN
       d. Bring sat host veth DOWN
    """
    try:
        pm = pid_map or {}
        sat_pid = _resolve_pid(iface.sat_pid, iface.sat_id, pm)
        gs_pid = _resolve_pid(iface.gs_pid, iface.gs_id, pm)
        if sat_pid:
            namespace_ops.remove_link_shaping(sat_pid, "gnd0")
        if gs_pid:
            namespace_ops.remove_link_shaping(gs_pid, "gnd0")
        if sat_pid:
            ground_bridge.detach_from_ground_bridge(iface.gs_id, iface.sat_id, sat_pid)
        return None
    except Exception as exc:
        msg = f"Ground down failed {iface.gs_id}<->{iface.sat_id}: {exc}"
        log.warning(msg)
        return msg


def _ground_link_up(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> str | None:
    """Bring up a ground link with bridge attach + shaping. Returns error or None.

    Sequence (matches realtime_dispatcher.py L338-344):
    1. attach_to_ground_bridge (host veths UP, sat gnd0 UP, mirred redirect)
    2. Apply tc shaping on GS gnd0
    3. Apply tc shaping on satellite gnd0
    4. If peer_mac present: NDP on gnd0 (synchronous, before ACK)
    """
    try:
        pm = pid_map or {}
        sat_pid = _resolve_pid(iface.sat_pid, iface.sat_id, pm)
        gs_pid = _resolve_pid(iface.gs_pid, iface.gs_id, pm)
        if sat_pid:
            ground_bridge.attach_to_ground_bridge(iface.gs_id, iface.sat_id, sat_pid)
        if gs_pid:
            namespace_ops.apply_link_shaping(gs_pid, "gnd0", iface.latency_ms, iface.bandwidth_mbps)
        if sat_pid:
            namespace_ops.apply_link_shaping(
                sat_pid, "gnd0", iface.latency_ms, iface.bandwidth_mbps
            )
        # NDP on gnd0 — synchronous, before ACK
        if iface.peer_mac and sat_pid:
            peer_ll = namespace_ops.mac_to_link_local(iface.peer_mac)
            namespace_ops.trigger_ndp_and_wait(sat_pid, "gnd0", peer_ll)
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
        pm = pid_map or {}
        if entry.link_type == node_agent_pb2.GROUND:
            gs_pid = _resolve_pid(entry.gs_pid, entry.gs_id, pm)
            sat_pid = _resolve_pid(entry.sat_pid, entry.sat_id, pm)
            if gs_pid:
                namespace_ops.update_delay(gs_pid, "gnd0", entry.latency_ms)
            if sat_pid:
                namespace_ops.update_delay(sat_pid, "gnd0", entry.latency_ms)
        else:
            pid = _resolve_pid(entry.pid, entry.node_id, pm)
            namespace_ops.update_delay(pid, entry.interface_name, entry.latency_ms)
        return None
    except Exception as exc:
        msg = f"Latency update failed ns({entry.pid})/{entry.interface_name}: {exc}"
        log.warning(msg)
        return msg


# ---------------------------------------------------------------------------
# RPC handler functions (called from server.py servicer)
# ---------------------------------------------------------------------------


def handle_batch_link_down(
    request: node_agent_pb2.BatchLinkDownRequest,
    context: grpc.ServicerContext,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.BatchLinkDownResponse:
    """Handle BatchLinkDown RPC."""
    if request.locality == node_agent_pb2.CROSS_NODE:
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("CROSS_NODE not implemented (M9)")
        return node_agent_pb2.BatchLinkDownResponse(
            success=False, error_message="CROSS_NODE not implemented"
        )

    start = _time.monotonic()
    errors: list[str] = []
    downed = 0

    # Submit all operations concurrently
    futures = {}
    pm = pid_map or {}
    for iface in request.interfaces:
        if iface.link_type == node_agent_pb2.GROUND:
            fut = _BATCH_POOL.submit(_ground_link_down, iface, pm)
        else:
            fut = _BATCH_POOL.submit(_isl_link_down, iface, pm)
        futures[fut] = iface

    # Collect results — wait for ALL before returning ACK
    for fut in as_completed(futures):
        iface = futures[fut]
        try:
            err = fut.result(timeout=10)
            if err is None:
                downed += 1
            else:
                errors.append(err)
        except Exception as exc:
            errors.append(f"Unexpected error for {iface.node_id}/{iface.interface_name}: {exc}")

    elapsed = (_time.monotonic() - start) * 1000
    error_msg = "; ".join(errors) if errors else ""
    if errors:
        log.warning(
            "BatchLinkDown %s: %d/%d downed (%.1fms): %s",
            request.batch_id,
            downed,
            len(request.interfaces),
            elapsed,
            error_msg,
        )
    else:
        log.info("BatchLinkDown %s: %d downed (%.1fms)", request.batch_id, downed, elapsed)

    return node_agent_pb2.BatchLinkDownResponse(
        success=not errors,
        error_message=error_msg,
        interfaces_downed=downed,
        apply_time_ms=elapsed,
    )


def _isl_link_up_phase1(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> str | None:
    """Phase 1 of ISL link-up: admin UP + shaping. No NDP yet.

    Returns error string or None.
    """
    try:
        pid = _resolve_pid(iface.pid, iface.node_id, pid_map or {})
        if iface.peer_mac:
            namespace_ops.disable_dad(pid, iface.interface_name)
        namespace_ops.set_interface_up(pid, iface.interface_name)
        namespace_ops.apply_link_shaping(
            pid, iface.interface_name, iface.latency_ms, iface.bandwidth_mbps
        )
        return None
    except Exception as exc:
        msg = f"ISL up phase1 failed ns({iface.pid})/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def _isl_ndp_phase2(
    iface: node_agent_pb2.InterfaceUp, pid_map: dict[str, int] | None = None
) -> str | None:
    """Phase 2 of ISL link-up: NDP resolution (synchronous).

    Called AFTER all interfaces in the batch are admin UP, so the peer's
    link-local is no longer tentative and NDP will resolve quickly.

    Uses a direct nsenter ping with a 2-second timeout. The ping itself
    triggers NDP and creates the neighbor entry. The kernel's NDP retransmit
    timer is ~1s (RFC 4861), so the first successful resolution takes ~1.1s
    on a fresh interface. On subsequent UP transitions with warm cache, it
    resolves in <100ms.
    """
    if not iface.peer_mac:
        return None
    try:
        import subprocess

        pid = _resolve_pid(iface.pid, iface.node_id, pid_map or {})
        peer_ll = namespace_ops.mac_to_link_local(iface.peer_mac)
        result = subprocess.run(
            [
                "nsenter",
                "--target",
                str(pid),
                "--net",
                "--",
                "ping",
                "-6",
                "-c",
                "1",
                "-W",
                "2",
                f"{peer_ll}%{iface.interface_name}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            log.debug("NDP resolved %s on %s in ns(%d)", peer_ll, iface.interface_name, iface.pid)
        else:
            log.warning(
                "NDP ping failed for %s on %s in ns(%d): %s",
                peer_ll,
                iface.interface_name,
                iface.pid,
                result.stderr.strip(),
            )
        return None
    except Exception as exc:
        msg = f"ISL NDP failed ns({iface.pid})/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def handle_batch_link_up(
    request: node_agent_pb2.BatchLinkUpRequest,
    context: grpc.ServicerContext,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.BatchLinkUpResponse:
    """Handle BatchLinkUp RPC.

    Two-phase execution for ISL links:
      Phase 1 (concurrent): admin UP + tc shaping on ALL interfaces
      Phase 2 (concurrent): NDP resolution on ALL interfaces

    Phase 2 runs after ALL phase 1 operations complete, ensuring every
    peer's link-local address is non-tentative before NDP solicitations
    are sent. Without this split, the first NS goes out while the peer
    is still in IPv6 DAD tentative state, causing a 1-second retransmit
    wait (RFC 4861).

    Ground links run their full sequence in phase 1 (they don't use the
    same NDP-after-UP pattern because attach_to_ground_bridge handles
    the UP transition internally).
    """
    if request.locality == node_agent_pb2.CROSS_NODE:
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("CROSS_NODE not implemented (M9)")
        return node_agent_pb2.BatchLinkUpResponse(
            success=False, error_message="CROSS_NODE not implemented"
        )

    start = _time.monotonic()
    errors: list[str] = []
    upped = 0

    # Separate ISL and ground interfaces
    isl_ifaces = [i for i in request.interfaces if i.link_type != node_agent_pb2.GROUND]
    gnd_ifaces = [i for i in request.interfaces if i.link_type == node_agent_pb2.GROUND]

    # Phase 1: admin UP + shaping (concurrent for all interfaces)
    pm = pid_map or {}
    phase1_futures = {}
    for iface in isl_ifaces:
        phase1_futures[_BATCH_POOL.submit(_isl_link_up_phase1, iface, pm)] = iface
    for iface in gnd_ifaces:
        phase1_futures[_BATCH_POOL.submit(_ground_link_up, iface, pm)] = iface

    # Wait for ALL phase 1 to complete
    isl_phase1_ok: list[node_agent_pb2.InterfaceUp] = []
    for fut in as_completed(phase1_futures):
        iface = phase1_futures[fut]
        try:
            err = fut.result(timeout=10)
            if err is None:
                if iface.link_type == node_agent_pb2.GROUND:
                    upped += 1  # Ground links are fully done after phase 1
                else:
                    isl_phase1_ok.append(iface)
            else:
                errors.append(err)
        except Exception as exc:
            errors.append(f"Phase1 error for {iface.node_id}/{iface.interface_name}: {exc}")

    # Phase 2: NDP resolution for ISL links that succeeded in phase 1
    # By now all peers are admin UP and non-tentative.
    if isl_phase1_ok:
        ndp_futures = {}
        for iface in isl_phase1_ok:
            ndp_futures[_BATCH_POOL.submit(_isl_ndp_phase2, iface, pm)] = iface

        for fut in as_completed(ndp_futures):
            iface = ndp_futures[fut]
            try:
                err = fut.result(timeout=30)
                if err is None:
                    upped += 1
                else:
                    # NDP failure is non-fatal — sidecar retry handles it
                    upped += 1  # Interface IS up, NDP just didn't resolve yet
                    log.warning(
                        "NDP did not resolve for %s/%s — sidecar retry will catch it",
                        iface.node_id,
                        iface.interface_name,
                    )
            except Exception as exc:
                upped += 1  # Interface IS up
                log.warning("NDP error for %s/%s: %s", iface.node_id, iface.interface_name, exc)

    elapsed = (_time.monotonic() - start) * 1000
    error_msg = "; ".join(errors) if errors else ""
    if errors:
        log.warning(
            "BatchLinkUp %s: %d/%d upped (%.1fms): %s",
            request.batch_id,
            upped,
            len(request.interfaces),
            elapsed,
            error_msg,
        )
    else:
        log.info("BatchLinkUp %s: %d upped (%.1fms)", request.batch_id, upped, elapsed)

    return node_agent_pb2.BatchLinkUpResponse(
        success=not errors,
        error_message=error_msg,
        interfaces_upped=upped,
        apply_time_ms=elapsed,
    )


def handle_set_latency(
    request: node_agent_pb2.SetLatencyRequest,
    context: grpc.ServicerContext,
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


def _read_psched_tick_factor() -> float:
    """Read kernel psched parameters for tc delay tick-to-microsecond conversion.

    Returns the factor to convert netem delay ticks to microseconds:
        delay_us = delay_ticks * factor

    Reads /proc/net/psched which contains: t2us us2t clock_res tick_res
    The conversion is: delay_us = delay_ticks * us2t / t2us
    """
    try:
        parts = open("/proc/net/psched").read().strip().split()
        t2us = int(parts[0], 16)
        us2t = int(parts[1], 16)
        if t2us > 0:
            return us2t / t2us
    except Exception:
        pass
    return 1.0  # Fallback: assume ticks = microseconds


# Cache the tick factor (constant for the lifetime of the process)
_TICK_TO_US = _read_psched_tick_factor()


def handle_get_topology(
    request: node_agent_pb2.GetTopologyRequest,
    context: grpc.ServicerContext,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.GetTopologyResponse:
    """Handle GetTopology RPC — return observed interface state.

    Enumerates ISL and ground interfaces in pod namespaces on this node
    and reports admin/oper state and current netem delay.
    Used for reconciliation on Scheduler restart.

    The pid_map is injected by the server (from PID discovery or --pid-map).
    """
    from pyroute2 import NetNS

    interfaces: list[node_agent_pb2.InterfaceState] = []
    pids = pid_map or {}

    # If pid_map is empty, try fresh discovery (handles startup timing)
    if not pids:
        try:
            from node_agent.pid_discovery import discover_local_pod_pids

            pids = discover_local_pod_pids()
        except Exception as exc:
            log.warning("GetTopology: PID discovery failed: %s", exc)

    for node_id, pid in pids.items():
        try:
            ns = NetNS(f"/proc/{pid}/ns/net")
            try:
                for link in ns.get_links():
                    ifname = link.get_attr("IFLA_IFNAME")
                    if ifname is None:
                        continue
                    if not (ifname.startswith("isl") or ifname.startswith("gnd")):
                        continue

                    flags = link["flags"]
                    admin_up = bool(flags & 0x1)  # IFF_UP
                    oper_up = bool(flags & 0x40)  # IFF_RUNNING

                    # Read netem delay from tc qdisc (pyroute2, no subprocess)
                    current_latency = 0.0
                    try:
                        idx = link["index"]
                        qdiscs = ns.get_qdiscs(index=idx)
                        for qd in qdiscs:
                            if qd.get_attr("TCA_KIND") == "netem":
                                opts = qd.get_attr("TCA_OPTIONS")
                                if opts:
                                    delay_ticks = opts.get("delay", 0)
                                    delay_us = delay_ticks * _TICK_TO_US
                                    current_latency = delay_us / 1000.0
                    except Exception:
                        pass

                    interfaces.append(
                        node_agent_pb2.InterfaceState(
                            node_id=node_id,
                            interface_name=ifname,
                            admin_up=admin_up,
                            oper_up=oper_up,
                            current_latency_ms=current_latency,
                        )
                    )
            finally:
                ns.close()
        except Exception as exc:
            log.warning("GetTopology: failed to read ns(%d) for %s: %s", pid, node_id, exc)

    return node_agent_pb2.GetTopologyResponse(interfaces=interfaces)
