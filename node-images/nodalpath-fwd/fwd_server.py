"""gRPC ForwardingService — kernel MPLS forwarding via iproute2.

Runs inside the nodalpath-fwd container. Receives full forwarding table
replacements from NodalPath and programs the Linux kernel MPLS dataplane
using `ip -f mpls route` commands.

All ISL and ground links are point-to-point veth pairs, so routes use
`dev {iface}` without `via` — there is exactly one peer on each link.

Atomicity: installs new/changed entries before removing stale ones.
State is only updated on full success.
"""

from __future__ import annotations

import logging
import os
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fwd-server")

GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))
NODE_ID = os.environ.get("NODE_ID", "unknown")

# In-memory state protected by lock
_lock = threading.Lock()
_state = {
    "topology_state_id": "",
    "sim_time": "",
    "lsr_entries": {},   # in_label -> LabelEntry
    "ler_entries": {},   # dst_prefix -> IngressEntry
    "last_update_ms": 0.0,
    "last_update_time": "",
}


def _iface_up(iface: str) -> bool:
    """Check if a network interface exists and is UP."""
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            return f.read().strip() in ("up", "unknown")
    except FileNotFoundError:
        return False


def _run_cmd(cmd: list[str]) -> None:
    """Run a shell command. Raises subprocess.CalledProcessError on failure."""
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _install_lsr(entry: pb2.LabelEntry) -> None:
    """Install or replace an LSR (label switching) entry in the kernel."""
    if entry.action == pb2.Action.SWAP:
        _run_cmd([
            "ip", "-f", "mpls", "route", "replace",
            str(entry.in_label), "as", str(entry.out_label),
            "dev", entry.out_interface,
        ])
    elif entry.action == pb2.Action.POP:
        _run_cmd([
            "ip", "-f", "mpls", "route", "replace",
            str(entry.in_label), "dev", entry.out_interface,
        ])


def _install_ler(entry: pb2.IngressEntry) -> None:
    """Install or replace an LER (ingress) entry in the kernel."""
    _run_cmd([
        "ip", "route", "replace", entry.dst_prefix,
        "encap", "mpls", str(entry.push_label),
        "dev", entry.out_interface,
    ])


def _remove_lsr(in_label: int) -> None:
    """Remove an LSR entry from the kernel."""
    _run_cmd(["ip", "-f", "mpls", "route", "del", str(in_label)])


def _remove_ler(dst_prefix: str) -> None:
    """Remove an LER entry from the kernel."""
    _run_cmd(["ip", "route", "del", dst_prefix])


class ForwardingServiceImpl(ForwardingServiceServicer):
    """gRPC service implementation for kernel MPLS forwarding."""

    def UpdateForwardingTable(self, request, context):
        start = time.monotonic()
        with _lock:
            return self._apply_update(request, start)

    def _apply_update(self, request, start: float):
        curr_lsr = dict(_state["lsr_entries"])
        curr_ler = dict(_state["ler_entries"])

        # Build next-state lookup dicts
        next_lsr = {}
        for entry in request.lsr_entries:
            next_lsr[entry.in_label] = entry
        next_ler = {}
        for entry in request.ler_entries:
            next_ler[entry.dst_prefix] = entry

        installed = 0
        skipped = 0
        errors = []

        # INSTALL phase: new or changed entries (skip if interface is down)
        for in_label, entry in next_lsr.items():
            if not _iface_up(entry.out_interface):
                skipped += 1
                continue
            old = curr_lsr.get(in_label)
            if old is None or not _lsr_eq(old, entry):
                try:
                    _install_lsr(entry)
                    installed += 1
                except subprocess.CalledProcessError as exc:
                    errors.append(f"LSR {in_label}: {exc.stderr.strip()}")

        for prefix, entry in next_ler.items():
            if not _iface_up(entry.out_interface):
                skipped += 1
                continue
            old = curr_ler.get(prefix)
            if old is None or not _ler_eq(old, entry):
                try:
                    _install_ler(entry)
                    installed += 1
                except subprocess.CalledProcessError as exc:
                    errors.append(f"LER {prefix}: {exc.stderr.strip()}")

        # REMOVE phase: stale entries
        for in_label in curr_lsr:
            if in_label not in next_lsr:
                try:
                    _remove_lsr(in_label)
                except subprocess.CalledProcessError:
                    pass  # entry may already be gone

        for prefix in curr_ler:
            if prefix not in next_ler:
                try:
                    _remove_ler(prefix)
                except subprocess.CalledProcessError:
                    pass

        # Update state (record the intended table even if some entries were skipped)
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
            request.topology_state_id, total, installed, skipped, len(errors), elapsed,
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
    """Compare two LabelEntry protos for equality."""
    return (
        a.in_label == b.in_label
        and a.action == b.action
        and a.out_label == b.out_label
        and a.out_interface == b.out_interface
    )


def _ler_eq(a, b) -> bool:
    """Compare two IngressEntry protos for equality."""
    return (
        a.dst_prefix == b.dst_prefix
        and a.push_label == b.push_label
        and a.out_interface == b.out_interface
    )


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    add_ForwardingServiceServicer_to_server(ForwardingServiceImpl(), server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("ForwardingService listening on port %d (node: %s)", GRPC_PORT, NODE_ID)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
