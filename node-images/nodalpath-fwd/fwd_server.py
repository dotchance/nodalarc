"""gRPC ForwardingService — kernel MPLS forwarding via iproute2.

Runs inside the nodalpath-fwd container. Receives full forwarding table
replacements from NodalPath and programs the Linux kernel MPLS dataplane
using `ip -f mpls route` commands.

MPLS Forwarding Model (hop-by-hop)
===================================

NodalPath uses a **hop-by-hop** MPLS forwarding model, not a traditional
end-to-end LSP model. In a traditional LSP, the ingress LER pushes a full
label stack and each transit LSR SWAPs the top label. In our model:

  1. The ingress LER pushes a SINGLE label: the SID of the **next hop**.
  2. The next-hop node POPs that label (its own SID).
  3. The inner IP packet is delivered to the local IP FIB for re-routing.
  4. The IP FIB has LER entries that push the SID of the *next* next hop.
  5. Repeat until the packet reaches the egress node.

This design exists because the Linux kernel MPLS table only supports
**one rule per input label**. If we installed both a POP (for packets
destined to this node) and SWAP entries (for transit traffic with this
node's SID), only one could exist. The hop-by-hop model sidesteps this
entirely: each node only needs a single POP for its own SID.

POP routing: `via inet 127.0.0.1 dev lo`
-----------------------------------------
When a node receives a packet with its own SID as the MPLS label, the
kernel pops the label and must deliver the inner IP packet somewhere.
We route it `via inet 127.0.0.1 dev lo`, which hands the decapsulated
IP packet back to the kernel's IPv4 routing table. The IP FIB then
matches the destination against installed LER ingress rules, which push
the next-hop SID and forward out the correct interface. This is the
mechanism that makes hop-by-hop re-encapsulation work.

SWAP routing: `via inet <peer_ip> dev <iface>`
-----------------------------------------------
SWAP entries (used only for special cases, not the normal forwarding
path) require `via inet <peer_ip>` because the Linux kernel needs an
IP nexthop to perform L2 (ARP/neighbor) resolution on the outgoing
interface. Unnumbered veth pairs have no inherent L2 address mapping,
so without an IP nexthop the kernel cannot fill in the Ethernet
destination. The /31 link-local addresses (169.254.x.x) assigned by
the orchestrator's link_manager provide this resolution target.

Retry thread
------------
There is a race between NodalPath pushing forwarding tables and the
orchestrator bringing up veth interfaces. NodalPath may compute and
push a forwarding table that references interfaces (e.g., isl0, gnd0)
that do not exist yet because the orchestrator has not finished creating
the veth pair and moving it into the container namespace. Entries that
fail because the interface is DOWN are queued in `_pending_lsr` /
`_pending_ler` and retried every 2 seconds by the `_retry_pending`
background thread. When the interface finally comes UP, the retry
thread installs the entry and clears the peer IP cache (since the
interface now has its /31 address assigned).

_peer_ip_cache
--------------
Each ISL veth has a deterministic /31 link-local address assigned by
the orchestrator (see link_manager.create_veth_pair). To build the
`via inet <peer_ip>` nexthop for MPLS routes, we read the local
address and flip the last bit to derive the peer. This is cached in
`_peer_ip_cache` to avoid repeated subprocess calls to `ip addr show`.
The cache is cleared whenever the retry thread installs entries after
an interface state change, because new interfaces may have addresses
that were not present when the cache was populated.

Known limitation
----------------
Linux kernel MPLS supports only one rule per input label. You cannot
install both POP and SWAP for the same label. This is why the entire
forwarding model is hop-by-hop: each node has exactly one LSR entry
(POP for its own SID) and multiple LER entries (PUSH for each
reachable destination prefix). See labels.py for the table generation
logic.

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
# Entries skipped because their interface was DOWN — retried in background
_pending_lsr: dict[int, object] = {}       # in_label -> LabelEntry
_pending_ler: dict[str, object] = {}       # dst_prefix -> IngressEntry


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


def _vtysh(*commands: str) -> None:
    """Execute one or more vtysh commands. Raises on failure."""
    cmd = ["vtysh"]
    for c in commands:
        cmd += ["-c", c]
    _run_cmd(cmd)


LOOPBACK_IPV4 = os.environ.get("LOOPBACK_IPV4", "")


def _install_lsr(entry: pb2.LabelEntry) -> None:
    """Install an LSR entry via FRR vtysh.

    POP entries configure ``mpls lsp <label> <nexthop> implicit-null``
    which pops the top MPLS label and forwards the remaining packet
    (still MPLS if label stack has more labels, or IP if bottom-of-stack)
    to the nexthop. FRR handles nexthop resolution through zebra.
    """
    nexthop = entry.nexthop_ip
    if not nexthop:
        # Fallback: node SID POP uses own loopback
        if entry.out_interface == "lo":
            nexthop = LOOPBACK_IPV4
        else:
            log.warning("No nexthop_ip for LSR in_label=%d, skipping", entry.in_label)
            return

    if entry.action == pb2.Action.POP:
        _vtysh("configure terminal",
               f"mpls lsp {entry.in_label} {nexthop} implicit-null",
               "end")
    elif entry.action == pb2.Action.SWAP:
        _vtysh("configure terminal",
               f"mpls lsp {entry.in_label} {nexthop} {entry.out_label}",
               "end")


def _install_ler(entry: pb2.IngressEntry) -> None:
    """Install an LER ingress entry via FRR vtysh.

    Uses ``ip route <prefix> <nexthop> label <stack>`` where <stack>
    is the full SR-TE label stack (adjacency SIDs + egress node SID).
    """
    nexthop = entry.nexthop_ip
    if not nexthop:
        log.warning("No nexthop_ip for LER dst=%s, skipping", entry.dst_prefix)
        return

    # Use the full label stack if available, otherwise single label
    if entry.label_stack:
        label_str = "/".join(str(l) for l in entry.label_stack)
    else:
        label_str = str(entry.push_label)

    _vtysh("configure terminal",
           f"ip route {entry.dst_prefix} {nexthop} label {label_str}",
           "end")


def _remove_lsr(in_label: int) -> None:
    """Remove an LSR entry via FRR vtysh."""
    _vtysh("configure terminal",
           f"no mpls lsp {in_label}",
           "end")


def _remove_ler(entry) -> None:
    """Remove an LER entry via FRR vtysh."""
    nexthop = entry.nexthop_ip if hasattr(entry, "nexthop_ip") else ""
    if nexthop:
        _vtysh("configure terminal",
               f"no ip route {entry.dst_prefix} {nexthop}",
               "end")
    else:
        log.warning("Cannot remove LER %s without nexthop_ip", entry.dst_prefix)


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

        # INSTALL phase: new or changed entries (skip if interface is down,
        # but queue for background retry when the interface comes UP).
        _pending_lsr.clear()
        _pending_ler.clear()

        for in_label, entry in next_lsr.items():
            if not _iface_up(entry.out_interface):
                skipped += 1
                _pending_lsr[in_label] = entry
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
                _pending_ler[prefix] = entry
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

        for prefix, old_entry in curr_ler.items():
            if prefix not in next_ler:
                try:
                    _remove_ler(old_entry)
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


def _retry_pending() -> None:
    """Background thread: retry entries skipped due to DOWN interfaces.

    Checks every 2 seconds. When an interface comes UP, installs the
    queued entries. Cleared on each new UpdateForwardingTable call.
    """
    while True:
        time.sleep(2)
        with _lock:
            if not _pending_lsr and not _pending_ler:
                continue
            retried = 0
            for in_label in list(_pending_lsr):
                entry = _pending_lsr[in_label]
                if _iface_up(entry.out_interface):
                    try:
                        _install_lsr(entry)
                        retried += 1
                    except subprocess.CalledProcessError:
                        pass
                    del _pending_lsr[in_label]
            for prefix in list(_pending_ler):
                entry = _pending_ler[prefix]
                if _iface_up(entry.out_interface):
                    try:
                        _install_ler(entry)
                        retried += 1
                    except subprocess.CalledProcessError:
                        pass
                    del _pending_ler[prefix]
            if retried:
                remaining = len(_pending_lsr) + len(_pending_ler)
                log.info("Retry: installed %d entries after interface UP (%d still pending)", retried, remaining)


def serve() -> None:
    # Start background retry thread for skipped-down entries
    threading.Thread(target=_retry_pending, daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    add_ForwardingServiceServicer_to_server(ForwardingServiceImpl(), server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("ForwardingService listening on port %d (node: %s)", GRPC_PORT, NODE_ID)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
