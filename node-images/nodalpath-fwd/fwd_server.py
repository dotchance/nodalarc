"""nodalpath-fwd satellite management agent.

Receives ForwardingTableUpdate gRPC messages from the NodalPath ground PCE and
programs the Linux kernel FIB directly via pyroute2, operating in policy routing
table 100 (isolated from FRR's main table).

MPLS forwarding model:

- SR-MPLS with label stacks per RFC 8402
- Adjacency SIDs (transit hops): AF_MPLS route, POP, forward out named interface
- Node SIDs (egress): AF_MPLS route, POP, deliver locally via loopback
- LER ingress (source nodes): IP route with MPLS encap, full label stack

Neighbor resolution:

MPLS routes use ``via inet6 fe80::<peer_link_local>`` for correct L2 frame
construction. The peer link-local is derived from the peer's MAC address via
EUI-64 transform and provided in the ForwardingTableUpdate proto.

Neighbor table management is the kernel's responsibility via NDP. The Topology
Observer triggers NDP solicitation and waits for REACHABLE state before emitting
LinkUp, ensuring the neighbor is resolved when routes are installed. This sidecar
never touches the neighbor table.

If a route install fails because the neighbor is not yet resolved (race condition),
the retry mechanism catches it. By retry time NDP has completed.

FRR role in NodalPath sessions:

FRR runs zebra and staticd for observability only. ``show mpls table`` and
``show ip route table 100`` reflect the kernel FIB state installed by this sidecar.
FRR does not install or modify forwarding state in NodalPath sessions.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import threading
import time
from concurrent import futures

import grpc
from proto import forwarding_pb2 as pb2
from proto.forwarding_pb2_grpc import (
    ForwardingServiceServicer,
    add_ForwardingServiceServicer_to_server,
)
from pyroute2 import IPRoute

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fwd-server")

GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))
NODE_ID = os.environ.get("NODE_ID", "unknown")

# Linux AF_MPLS constant (not in Python's socket module)
AF_MPLS = 28

# Policy routing table for NodalPath forwarding state, isolated from FRR/main table.
# Clean teardown = flush this table. No interference with IS-IS/OSPF in other modes.
POLICY_TABLE = int(os.environ.get("NODALPATH_TABLE", "100"))

# In-memory state protected by lock
_lock = threading.Lock()
_state = {
    "topology_state_id": "",
    "sim_time": "",
    "lsr_entries": {},  # in_label -> LabelEntry
    "ler_entries": {},  # dst_prefix -> IngressEntry
    "last_update_ms": 0.0,
    "last_update_time": "",
}
# Entries skipped because their interface was DOWN — retried in background
_pending_lsr: dict[int, object] = {}  # in_label -> LabelEntry
_pending_ler: dict[str, object] = {}  # dst_prefix -> IngressEntry

# Shared IPRoute handle (created once, reused).
_ipr: IPRoute | None = None


def _get_ipr() -> IPRoute:
    global _ipr
    if _ipr is None:
        _ipr = IPRoute()
    return _ipr


def _ensure_policy_rule() -> None:
    """Install ip rule so all traffic uses the NodalPath policy table."""
    ipr = _get_ipr()
    rules = ipr.get_rules(family=2)  # AF_INET
    for rule in rules:
        for attr_name, attr_val in rule["attrs"]:
            if attr_name == "FRA_TABLE" and attr_val == POLICY_TABLE:
                return
    try:
        ipr.rule("add", table=POLICY_TABLE, priority=100)
        log.info("Installed ip rule: from all lookup %d pref 100", POLICY_TABLE)
    except Exception as exc:
        log.warning("Failed to install ip rule: %s", exc)


def _iface_up(iface: str) -> bool:
    """Check if a network interface exists and is UP."""
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            return f.read().strip() in ("up", "unknown")
    except FileNotFoundError:
        return False


def _iface_index(iface: str) -> int:
    """Get the kernel interface index for a named interface."""
    ipr = _get_ipr()
    links = ipr.link_lookup(ifname=iface)
    if not links:
        raise FileNotFoundError(f"Interface {iface} not found")
    return links[0]


# ---------------------------------------------------------------------------
# MPLS route installation via pyroute2 netlink
# ---------------------------------------------------------------------------


def _install_lsr(entry: pb2.LabelEntry) -> None:
    """Install an LSR entry via netlink.

    POP: pop the top MPLS label and forward out the specified interface.
    Uses ``via inet6 <peer_link_local>`` for L2 resolution. The neighbor
    is already REACHABLE via NDP (the TO waited for it before LinkUp).

    Node SID POP (out_interface="lo"): deliver locally via loopback.
    """
    ipr = _get_ipr()
    oif = _iface_index(entry.out_interface)

    if entry.out_interface == "lo":
        via = {"family": socket.AF_INET, "addr": "127.0.0.1"}
    elif entry.nexthop_ll:
        via = {"family": socket.AF_INET6, "addr": entry.nexthop_ll}
    else:
        log.warning(
            "No nexthop_ll for LSR in_label=%d dev %s, using bare oif",
            entry.in_label,
            entry.out_interface,
        )
        via = None

    if entry.action == pb2.Action.POP:
        kwargs = {
            "family": AF_MPLS,
            "dst": {"label": entry.in_label, "bos": 1},
            "oif": oif,
        }
        if via:
            kwargs["via"] = via
        ipr.route("replace", **kwargs)

    elif entry.action == pb2.Action.SWAP:
        kwargs = {
            "family": AF_MPLS,
            "dst": {"label": entry.in_label, "bos": 1},
            "oif": oif,
            "newdst": {"label": entry.out_label, "bos": 1},
        }
        if via:
            kwargs["via"] = via
        ipr.route("replace", **kwargs)


def _install_ler(entry: pb2.IngressEntry) -> None:
    """Install an LER ingress entry via netlink.

    Pushes the full SR-TE label stack onto IP packets matching the
    destination prefix. Uses ``via inet6 <peer_ll>`` for L2 resolution,
    same as LSR routes — the kernel needs a resolved neighbor to
    construct the outgoing Ethernet frame for MPLS-encapped packets.
    """
    ipr = _get_ipr()
    oif = _iface_index(entry.out_interface)

    labels = list(entry.label_stack) if entry.label_stack else [entry.push_label]

    dst, prefix_len = entry.dst_prefix.split("/")

    kwargs = {
        "table": POLICY_TABLE,
        "dst": dst,
        "dst_len": int(prefix_len),
        "oif": oif,
        "encap": {"type": "mpls", "labels": labels},
    }
    if entry.nexthop_ll:
        kwargs["via"] = {"family": socket.AF_INET6, "addr": entry.nexthop_ll}

    ipr.route("replace", **kwargs)


def _remove_lsr(in_label: int) -> None:
    """Remove an LSR entry via netlink."""
    ipr = _get_ipr()
    with contextlib.suppress(Exception):
        ipr.route("del", family=AF_MPLS, dst={"label": in_label, "bos": 1})


def _remove_ler(entry) -> None:
    """Remove an LER entry via netlink."""
    ipr = _get_ipr()
    try:
        dst, prefix_len = entry.dst_prefix.split("/")
        ipr.route("del", table=POLICY_TABLE, dst=dst, dst_len=int(prefix_len))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# gRPC ForwardingService implementation
# ---------------------------------------------------------------------------


class ForwardingServiceImpl(ForwardingServiceServicer):
    """gRPC service implementation for kernel MPLS forwarding."""

    def UpdateForwardingTable(self, request, context):
        start = time.monotonic()
        with _lock:
            return self._apply_update(request, start)

    def _apply_update(self, request, start: float):
        curr_lsr = dict(_state["lsr_entries"])
        curr_ler = dict(_state["ler_entries"])

        next_lsr = {}
        for entry in request.lsr_entries:
            next_lsr[entry.in_label] = entry
        next_ler = {}
        for entry in request.ler_entries:
            next_ler[entry.dst_prefix] = entry

        installed = 0
        skipped = 0
        errors = []

        # INSTALL phase: new or changed entries (skip if interface is down,
        # queue for background retry when the interface comes UP).
        _pending_lsr.clear()
        _pending_ler.clear()

        for in_label, entry in next_lsr.items():
            if entry.out_interface != "lo" and not _iface_up(entry.out_interface):
                skipped += 1
                _pending_lsr[in_label] = entry
                continue
            old = curr_lsr.get(in_label)
            if old is None or not _lsr_eq(old, entry):
                try:
                    _install_lsr(entry)
                    installed += 1
                except subprocess.CalledProcessError as exc:
                    errors.append(
                        f"LSR {in_label}: {exc.stderr.strip() if hasattr(exc, 'stderr') else exc}"
                    )
                except Exception as exc:
                    errors.append(f"LSR {in_label}: {exc}")

        for prefix, entry in next_ler.items():
            if not _iface_up(entry.out_interface):
                skipped += 1
                _pending_ler[prefix] = entry
                continue
            old = curr_ler.get(prefix)
            if old is None or not _ler_eq(old, entry):
                try:
                    _install_ler(entry)
                    installed += 1
                except subprocess.CalledProcessError as exc:
                    errors.append(
                        f"LER {prefix}: {exc.stderr.strip() if hasattr(exc, 'stderr') else exc}"
                    )
                except Exception as exc:
                    errors.append(f"LER {prefix}: {exc}")

        # REMOVE phase: stale entries
        for in_label in curr_lsr:
            if in_label not in next_lsr:
                with contextlib.suppress(Exception):
                    _remove_lsr(in_label)

        for prefix, old_entry in curr_ler.items():
            if prefix not in next_ler:
                with contextlib.suppress(Exception):
                    _remove_ler(old_entry)

        # Update state
        _state["lsr_entries"] = next_lsr
        _state["ler_entries"] = next_ler
        _state["topology_state_id"] = request.topology_state_id
        _state["sim_time"] = request.sim_time
        elapsed = (time.monotonic() - start) * 1000
        _state["last_update_ms"] = elapsed
        _state["last_update_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        total = len(next_lsr) + len(next_ler)
        log.info(
            "Applied update %s: %d entries (%d installed, %d skipped-down, %d errors) in %.1fms",
            request.topology_state_id,
            total,
            installed,
            skipped,
            len(errors),
            elapsed,
        )

        success = len(errors) == 0
        error_msg = "; ".join(errors) if errors else ""
        return pb2.PushResponse(
            success=success,
            error_message=error_msg,
            entries_installed=installed,
            apply_time_ms=elapsed,
        )

    def GetForwardingTable(self, request, context):
        with _lock:
            lsr = list(_state["lsr_entries"].values())
            ler = list(_state["ler_entries"].values())
            return pb2.ForwardingTableState(
                topology_state_id=_state["topology_state_id"],
                sim_time=_state["sim_time"],
                lsr_entries=lsr,
                ler_entries=ler,
            )

    def GetStatus(self, request, context):
        with _lock:
            total = len(_state["lsr_entries"]) + len(_state["ler_entries"])
            return pb2.NodeStatus(
                node_id=NODE_ID,
                current_topology_state_id=_state["topology_state_id"],
                total_entries=total,
                last_update_ms=_state["last_update_ms"],
                last_update_time=_state["last_update_time"],
            )


def _lsr_eq(a, b) -> bool:
    return (
        a.in_label == b.in_label
        and a.action == b.action
        and a.out_label == b.out_label
        and a.out_interface == b.out_interface
    )


def _ler_eq(a, b) -> bool:
    return (
        a.dst_prefix == b.dst_prefix
        and a.push_label == b.push_label
        and a.out_interface == b.out_interface
    )


# ---------------------------------------------------------------------------
# Background retry for entries skipped due to DOWN interfaces
# ---------------------------------------------------------------------------


def _retry_pending() -> None:
    """Retry entries skipped due to DOWN interfaces.

    Checks every 2 seconds. When an interface comes UP, installs the
    queued entries. The neighbor should be REACHABLE by this point
    (the TO waited for NDP before emitting LinkUp).
    """
    while True:
        time.sleep(2)
        with _lock:
            if not _pending_lsr and not _pending_ler:
                continue
            retried = 0
            for in_label in list(_pending_lsr):
                entry = _pending_lsr[in_label]
                if entry.out_interface == "lo" or _iface_up(entry.out_interface):
                    try:
                        _install_lsr(entry)
                        retried += 1
                    except Exception as exc:
                        log.warning("Retry failed for LSR %d: %s", in_label, exc)
                    del _pending_lsr[in_label]
            for prefix in list(_pending_ler):
                entry = _pending_ler[prefix]
                if _iface_up(entry.out_interface):
                    try:
                        _install_ler(entry)
                        retried += 1
                    except Exception as exc:
                        log.warning("Retry failed for LER %s: %s", prefix, exc)
                    del _pending_ler[prefix]
            if retried:
                remaining = len(_pending_lsr) + len(_pending_ler)
                log.info(
                    "Retry: installed %d entries after interface UP (%d still pending)",
                    retried,
                    remaining,
                )


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def serve() -> None:
    _ensure_policy_rule()
    threading.Thread(target=_retry_pending, daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    add_ForwardingServiceServicer_to_server(ForwardingServiceImpl(), server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("ForwardingService listening on port %d (node: %s)", GRPC_PORT, NODE_ID)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
