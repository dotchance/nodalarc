"""Node Agent RPC handler implementations.

Executes kernel operations dispatched by scheduler/dispatcher.py via
ZMQ ROUTER/DEALER. Uses namespace_ops.py and ground_bridge.py for
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


# ---------------------------------------------------------------------------
# Per-link operation functions (called concurrently within batches)
# ---------------------------------------------------------------------------


def _isl_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Admin-down a single ISL interface. Returns error string or None."""
    try:
        pid = _require_pid(iface.node_id, pid_map or {})
        namespace_ops.set_interface_down(pid, iface.interface_name)
        return None
    except Exception as exc:
        msg = f"ISL down failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def _ground_link_down(
    iface: node_agent_pb2.InterfaceDown, pid_map: dict[str, int] | None = None
) -> str | None:
    """Tear down a ground link. Returns error string or None.

    PRD v0.42 Section 13.6 LinkDown sequence:
    1. Remove tc mirred redirect from host-side veths
    2. Bring satellite host-side veth DOWN — carrier drops on GS gnd0
       automatically (UP → LOWERLAYERDOWN), FRR tears down adjacency
    3. Bring satellite gnd0 DOWN

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.
    """
    try:
        pm = pid_map or {}
        sat_pid = _require_pid(iface.sat_id, pm)
        gs_pid = _require_pid(iface.gs_id, pm)
        namespace_ops.remove_link_shaping(sat_pid, "gnd0")
        namespace_ops.remove_link_shaping(gs_pid, "gnd0")
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

    PRD v0.42 Section 13.6 LinkUp sequence:
    1. attach_to_ground_bridge (host veths UP, sat gnd0 UP, mirred redirect)
       — carrier arrives on GS gnd0 automatically (LOWERLAYERDOWN → UP)
    2. Apply tc shaping on GS gnd0
    3. Apply tc shaping on satellite gnd0
    4. If peer_mac present: NDP on gnd0 (synchronous, before ACK)

    No explicit admin state manipulation on GS gnd0 — host-side veth state
    drives carrier which drives FRR behavior.
    """
    try:
        pm = pid_map or {}
        sat_pid = _require_pid(iface.sat_id, pm)
        gs_pid = _require_pid(iface.gs_id, pm)
        ground_bridge.attach_to_ground_bridge(iface.gs_id, iface.sat_id, sat_pid)
        namespace_ops.apply_link_shaping(gs_pid, "gnd0", iface.latency_ms, iface.bandwidth_mbps)
        namespace_ops.apply_link_shaping(sat_pid, "gnd0", iface.latency_ms, iface.bandwidth_mbps)
        if iface.peer_mac:
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
            gs_pid = _require_pid(entry.gs_id, pm)
            sat_pid = _require_pid(entry.sat_id, pm)
            namespace_ops.update_delay(gs_pid, "gnd0", entry.latency_ms)
            namespace_ops.update_delay(sat_pid, "gnd0", entry.latency_ms)
        else:
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
    """Handle BatchLinkDown — per-interface locality (PRD Decision 1)."""
    start = _time.monotonic()
    errors: list[str] = []
    downed = 0

    # Submit all operations concurrently — each interface carries its own locality
    futures = {}
    pm = pid_map or {}
    for iface in request.interfaces:
        if iface.locality == node_agent_pb2.CROSS_NODE and iface.vni:
            if iface.link_type == node_agent_pb2.GROUND:
                # CROSS_NODE GROUND: detach VXLAN from existing host-side interface
                is_sat = not iface.node_id.startswith("gs-")
                if is_sat:
                    host_ifname = ground_bridge._sat_gnd_host_name(iface.node_id)
                    sat_pid = pm.get(iface.node_id, 0)
                else:
                    host_ifname = ground_bridge._gs_bridge_port_name(iface.node_id)
                    sat_pid = None
                fut = _BATCH_POOL.submit(
                    vxlan.detach_cross_node_ground,
                    host_ifname,
                    iface.vni,
                    sat_pid if is_sat else None,
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
        pid = _require_pid(iface.node_id, pid_map or {})
        if iface.peer_mac:
            namespace_ops.disable_dad(pid, iface.interface_name)
        namespace_ops.set_interface_up(pid, iface.interface_name)
        namespace_ops.apply_link_shaping(
            pid, iface.interface_name, iface.latency_ms, iface.bandwidth_mbps
        )
        return None
    except Exception as exc:
        msg = f"ISL up phase1 failed {iface.node_id}/{iface.interface_name}: {exc}"
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

        pid = _require_pid(iface.node_id, pid_map or {})
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
            log.debug("NDP resolved %s on %s for %s", peer_ll, iface.interface_name, iface.node_id)
        else:
            log.warning(
                "NDP ping failed for %s on %s for %s: %s",
                peer_ll,
                iface.interface_name,
                iface.node_id,
                result.stderr.strip(),
            )
        return None
    except Exception as exc:
        msg = f"ISL NDP failed {iface.node_id}/{iface.interface_name}: {exc}"
        log.warning(msg)
        return msg


def handle_batch_link_up(
    request: node_agent_pb2.BatchLinkUpRequest,
    context=None,
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
    start = _time.monotonic()
    errors: list[str] = []
    upped = 0
    pm = pid_map or {}

    # Validate PIDs. Per-interface locality (PRD Decision 1):
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
        log.error("BatchLinkUp %s REJECTED: %s", request.batch_id, msg)
        return node_agent_pb2.BatchLinkUpResponse(
            success=False,
            error_message=msg,
            interfaces_upped=0,
            apply_time_ms=0,
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
    for iface in request.interfaces:
        if iface.locality != node_agent_pb2.CROSS_NODE:
            continue
        if not iface.remote_node_ip or not iface.vni:
            link_desc = "GROUND" if iface.link_type == node_agent_pb2.GROUND else "ISL"
            errors.append(
                f"CROSS_NODE {link_desc} {iface.node_id}/{iface.interface_name}: "
                f"missing remote_node_ip or vni"
            )
            continue
        if local_ip is None:
            local_ip = _discover_local_ip()

        if iface.link_type == node_agent_pb2.GROUND:
            # GROUND: attach via existing host-side infrastructure
            try:
                # Determine local host-side interface name
                is_sat = not iface.node_id.startswith("gs-")
                if is_sat:
                    host_ifname = ground_bridge._sat_gnd_host_name(iface.node_id)
                    sat_pid = pm.get(iface.node_id, 0)
                else:
                    host_ifname = ground_bridge._gs_bridge_port_name(iface.node_id)
                    sat_pid = None
                vxlan.attach_cross_node_ground(
                    local_host_ifname=host_ifname,
                    local_ip=local_ip,
                    remote_ip=iface.remote_node_ip,
                    vni=iface.vni,
                    sat_pid=sat_pid if is_sat else None,
                )
                cross_node_ground.append(iface)
            except Exception as exc:
                errors.append(
                    f"VXLAN ground attach failed {iface.node_id}/{iface.interface_name}: {exc}"
                )
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
            except Exception as exc:
                errors.append(f"VXLAN create failed {iface.node_id}/{iface.interface_name}: {exc}")

    # Phase 1: Bring interfaces UP + apply shaping.
    # - LOCAL GROUND: bridge + mirred attach (_ground_link_up)
    # - CROSS_NODE GROUND: fully handled in Phase 0 — skip
    # - LOCAL/CROSS_NODE ISL: UP + shaping (_isl_link_up_phase1)
    phase1_futures = {}
    for iface in request.interfaces:
        if iface in cross_node_ground:
            continue  # Fully handled in Phase 0
        if iface.link_type == node_agent_pb2.GROUND and iface.locality == node_agent_pb2.LOCAL:
            phase1_futures[_BATCH_POOL.submit(_ground_link_up, iface, pm)] = iface
        else:
            phase1_futures[_BATCH_POOL.submit(_isl_link_up_phase1, iface, pm)] = iface

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

    # Count cross-node ground links handled in Phase 0
    upped += len(cross_node_ground)

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
    context=None,
    pid_map: dict[str, int] | None = None,
) -> node_agent_pb2.GetTopologyResponse:
    """Handle GetTopology RPC — return observed interface state.

    Enumerates ISL and ground interfaces in pod namespaces on this node
    and reports admin/oper state and current netem delay.
    Used for reconciliation on Scheduler restart.

    The pid_map is injected by the server (from PID discovery or --pid-map).
    """
    from pyroute2 import IPRoute

    from node_agent.namespace_ops import _in_namespace

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

            def _read_interfaces(ipr: IPRoute, _node_id: str = node_id) -> list:
                result = []
                for link in ipr.get_links():
                    ifname = link.get_attr("IFLA_IFNAME")
                    if ifname is None:
                        continue
                    if not (ifname.startswith("isl") or ifname.startswith("gnd")):
                        continue

                    flags = link["flags"]
                    admin_up = bool(flags & 0x1)
                    oper_up = bool(flags & 0x40)

                    current_latency = 0.0
                    try:
                        idx = link["index"]
                        qdiscs = ipr.get_qdiscs(index=idx)
                        for qd in qdiscs:
                            if qd.get_attr("TCA_KIND") == "netem":
                                opts = qd.get_attr("TCA_OPTIONS")
                                if opts:
                                    delay_ticks = opts.get("delay", 0)
                                    delay_us = delay_ticks * _TICK_TO_US
                                    current_latency = delay_us / 1000.0
                    except Exception:
                        pass

                    result.append(
                        node_agent_pb2.InterfaceState(
                            node_id=_node_id,
                            interface_name=ifname,
                            admin_up=admin_up,
                            oper_up=oper_up,
                            current_latency_ms=current_latency,
                        )
                    )
                return result

            interfaces.extend(_in_namespace(pid, _read_interfaces))
        except Exception as exc:
            log.warning("GetTopology: failed to read ns(%d) for %s: %s", pid, node_id, exc)

    return node_agent_pb2.GetTopologyResponse(interfaces=interfaces)
