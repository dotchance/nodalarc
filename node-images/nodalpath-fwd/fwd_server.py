"""gRPC ForwardingService — kernel MPLS forwarding via iproute2.

Runs inside the nodalpath-fwd container. Receives full forwarding table
replacements from NodalPath and programs the Linux kernel MPLS dataplane
using `ip -f mpls route` commands.

Atomicity: installs new/changed entries before removing stale ones.
State is only updated on full success.
"""

from __future__ import annotations

import json
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
PEERS_PATH = os.environ.get("PEERS_PATH", "/etc/nodalpath/peers.json")

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

# Peer IP map: interface name -> peer IP address
_peers: dict[str, str] = {}


def _load_peers() -> None:
    """Load peer IP map from ConfigMap-mounted JSON file."""
    global _peers
    try:
        with open(PEERS_PATH) as f:
            _peers = json.load(f)
        log.info("Loaded %d peer mappings from %s", len(_peers), PEERS_PATH)
    except FileNotFoundError:
        log.warning("Peers file not found at %s — nexthop resolution will fail", PEERS_PATH)
    except Exception as exc:
        log.warning("Failed to load peers from %s: %s", PEERS_PATH, exc)


def _nexthop(iface: str) -> str:
    """Resolve an interface name to its peer's IP address."""
    ip = _peers.get(iface)
    if ip is None:
        raise ValueError(f"No peer IP for interface {iface}")
    return ip


def _run_cmd(cmd: list[str]) -> None:
    """Run a shell command. Raises subprocess.CalledProcessError on failure."""
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _install_lsr(entry: pb2.LabelEntry) -> None:
    """Install or replace an LSR (label switching) entry in the kernel."""
    nh = _nexthop(entry.out_interface)
    if entry.action == pb2.Action.SWAP:
        _run_cmd([
            "ip", "-f", "mpls", "route", "replace",
            str(entry.in_label), "via", "inet", nh,
            "as", str(entry.out_label), "dev", entry.out_interface,
        ])
    elif entry.action == pb2.Action.POP:
        _run_cmd([
            "ip", "-f", "mpls", "route", "replace",
            str(entry.in_label), "via", "inet", nh,
            "dev", entry.out_interface,
        ])


def _install_ler(entry: pb2.IngressEntry) -> None:
    """Install or replace an LER (ingress) entry in the kernel."""
    nh = _nexthop(entry.out_interface)
    _run_cmd([
        "ip", "route", "replace", entry.dst_prefix,
        "encap", "mpls", str(entry.push_label),
        "via", nh, "dev", entry.out_interface,
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

        try:
            # INSTALL phase: new or changed entries
            for in_label, entry in next_lsr.items():
                old = curr_lsr.get(in_label)
                if old is None or not _lsr_eq(old, entry):
                    _install_lsr(entry)
                    installed += 1

            for prefix, entry in next_ler.items():
                old = curr_ler.get(prefix)
                if old is None or not _ler_eq(old, entry):
                    _install_ler(entry)
                    installed += 1

            # REMOVE phase: stale entries
            for in_label in curr_lsr:
                if in_label not in next_lsr:
                    _remove_lsr(in_label)

            for prefix in curr_ler:
                if prefix not in next_ler:
                    _remove_ler(prefix)

        except subprocess.CalledProcessError as exc:
            elapsed = (time.monotonic() - start) * 1000
            log.error("Kernel command failed: %s (stderr: %s)", exc.cmd, exc.stderr)
            return pb2.PushResponse(
                success=False,
                error_message=f"Command failed: {exc.stderr}",
                entries_installed=installed,
                apply_time_ms=elapsed,
            )

        # Update state on success
        _state["lsr_entries"] = next_lsr
        _state["ler_entries"] = next_ler
        _state["topology_state_id"] = request.topology_state_id
        _state["sim_time"] = request.sim_time
        elapsed = (time.monotonic() - start) * 1000
        _state["last_update_ms"] = elapsed
        _state["last_update_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        total = len(next_lsr) + len(next_ler)
        log.info(
            "Applied update %s: %d entries (%d installed/changed) in %.1fms",
            request.topology_state_id, total, installed, elapsed,
        )

        return pb2.PushResponse(
            success=True,
            error_message="",
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
    _load_peers()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    add_ForwardingServiceServicer_to_server(ForwardingServiceImpl(), server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("ForwardingService listening on port %d (node: %s)", GRPC_PORT, NODE_ID)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
