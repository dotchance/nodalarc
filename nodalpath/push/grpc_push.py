"""gRPC transport for pushing forwarding tables to nodalpath-fwd containers.

Uses a module-level channel cache to reuse gRPC channels across push cycles.
Channels are created on first use and kept alive with HTTP/2 keepalives.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import grpc

from nodalpath.models.almanac import ForwardingTable
from nodalpath.proto import (
    Action,
    ForwardingTableUpdate,
    IngressEntry,
    LabelEntry,
)
from nodalpath.proto.forwarding_pb2_grpc import ForwardingServiceStub

# Channel cache — reuse gRPC channels across push cycles to avoid
# TCP connection storm (51 fresh connections per push × 3 pushes/min).
_channel_cache: dict[str, grpc.Channel] = {}


def _get_channel(target: str) -> grpc.Channel:
    """Get or create a cached gRPC channel for the given target."""
    if target in _channel_cache:
        state = _channel_cache[target].connectivity_state(try_to_connect=False)
        if state == grpc.ChannelConnectivity.SHUTDOWN:
            del _channel_cache[target]
    if target not in _channel_cache:
        _channel_cache[target] = grpc.insecure_channel(
            target,
            options=[
                ("grpc.keepalive_time_ms", 10000),
                ("grpc.keepalive_timeout_ms", 5000),
                ("grpc.http2.max_pings_without_data", 0),
                ("grpc.http2.min_time_between_pings_ms", 10000),
            ],
        )
    return _channel_cache[target]


log = logging.getLogger(__name__)


def _deterministic_mac(node_id: str, ifname: str) -> str:
    """Derive the deterministic MAC for a peer interface.

    Must match orchestrator/link_manager.py:deterministic_mac exactly.
    """
    digest = hashlib.sha256(f"{node_id}:{ifname}".encode()).digest()
    return f"02:{digest[0]:02x}:{digest[1]:02x}:{digest[2]:02x}:{digest[3]:02x}:{digest[4]:02x}"


def _mac_to_link_local(mac_str: str) -> str:
    """Derive canonical IPv6 link-local from MAC via EUI-64.

    '02:ee:d2:0a:a9:36' → 'fe80::ee:d2ff:fe0a:a936'
    """
    parts = [int(x, 16) for x in mac_str.split(":")]
    parts[0] ^= 0x02  # flip U/L bit
    eui64 = parts[:3] + [0xFF, 0xFE] + parts[3:]
    groups = [
        f"{eui64[0]:02x}{eui64[1]:02x}",
        f"{eui64[2]:02x}{eui64[3]:02x}",
        f"{eui64[4]:02x}{eui64[5]:02x}",
        f"{eui64[6]:02x}{eui64[7]:02x}",
    ]
    raw = "fe80::" + ":".join(groups)
    return str(ipaddress.IPv6Address(raw))  # canonical form (drop leading zeros)


def _default_grpc_port() -> int:
    from nodalarc.platform import get_platform_config

    return get_platform_config().nodalpath_fwd_grpc_port


def _default_timeout() -> float:
    from nodalpath.platform import get_nodalpath_config

    return float(get_nodalpath_config().grpc_push_timeout_seconds)


def _max_workers() -> int:
    from nodalpath.platform import get_nodalpath_config

    return get_nodalpath_config().grpc_push_max_parallel_workers


@dataclass
class GrpcExecResult:
    """Result of a gRPC forwarding table push."""

    node_id: str
    pod_ip: str
    success: bool
    entries_installed: int
    apply_time_ms: float
    error_message: str


def build_forwarding_update(
    table: ForwardingTable,
    topology_state_id: str,
    sim_time: str,
    iface_to_peer_loopback: dict[tuple[str, str], str] | None = None,
    own_loopback: str = "",
    iface_to_peer_info: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> ForwardingTableUpdate:
    """Convert a ForwardingTable to a gRPC ForwardingTableUpdate message.

    Always sends the full table — no deltas.

    iface_to_peer_info: maps (node_id, interface_name) → (peer_node_id, peer_iface_name).
        Used to compute the peer's deterministic MAC and IPv6 link-local
        for PERMANENT neighbor entries and ``via inet6`` MPLS nexthops.

    iface_to_peer_loopback: maps (node_id, interface_name) → peer loopback IP.
    own_loopback: this node's own loopback IP (for node SID POP → local delivery).
    """
    peer_map = iface_to_peer_loopback or {}
    peer_info = iface_to_peer_info or {}
    node_id = table.node_id

    def _peer_ll_mac(iface: str) -> tuple[str, str]:
        """Compute peer's link-local and MAC for an interface."""
        info = peer_info.get((node_id, iface))
        if not info:
            return ("", "")
        peer_nid, peer_iface = info
        mac = _deterministic_mac(peer_nid, peer_iface)
        ll = _mac_to_link_local(mac)
        return (ll, mac)

    lsr_entries: list[LabelEntry] = []
    for binding in table.lsr_bindings:
        if binding.action == "pop":
            if binding.out_interface == "lo":
                ll, mac = "", ""
            else:
                ll, mac = _peer_ll_mac(binding.out_interface)
            entry = LabelEntry(
                in_label=binding.in_label,
                action=Action.POP,
                out_label=0,
                out_interface=binding.out_interface,
                nexthop_ll=ll,
                nexthop_mac=mac,
            )
            lsr_entries.append(entry)
        elif binding.action == "swap":
            ll, mac = _peer_ll_mac(binding.out_interface)
            entry = LabelEntry(
                in_label=binding.in_label,
                action=Action.SWAP,
                out_label=binding.out_label or 0,
                out_interface=binding.out_interface,
                nexthop_ll=ll,
                nexthop_mac=mac,
            )
            lsr_entries.append(entry)
        else:
            log.warning(
                "Skipping push action for in_label=%d on %s",
                binding.in_label,
                table.node_id,
            )

    ler_entries: list[IngressEntry] = []
    for rule in table.ler_ingress_rules:
        ll, mac = _peer_ll_mac(rule.out_interface)
        entry = IngressEntry(
            dst_prefix=rule.dst_prefix,
            push_label=rule.push_label,
            out_interface=rule.out_interface,
            nexthop_ll=ll,
            nexthop_mac=mac,
            label_stack=list(getattr(rule, "label_stack", []) or []),
            backup_push_label=rule.backup_push_label or 0,
            backup_out_interface=rule.backup_out_interface or "",
        )
        ler_entries.append(entry)

    return ForwardingTableUpdate(
        topology_state_id=topology_state_id,
        sim_time=sim_time,
        lsr_entries=lsr_entries,
        ler_entries=ler_entries,
    )


def push_forwarding_table(
    node_id: str,
    pod_ip: str,
    update: ForwardingTableUpdate,
    port: int | None = None,
    timeout: float | None = None,
) -> GrpcExecResult:
    """Push a forwarding table to a single node via gRPC.

    Never raises. All errors are captured in GrpcExecResult.
    """
    if port is None:
        port = _default_grpc_port()
    if timeout is None:
        timeout = _default_timeout()
    target = f"{pod_ip}:{port}"
    channel = _get_channel(target)
    try:
        stub = ForwardingServiceStub(channel)
        response = stub.UpdateForwardingTable(update, timeout=timeout)
        return GrpcExecResult(
            node_id=node_id,
            pod_ip=pod_ip,
            success=response.success,
            entries_installed=response.entries_installed,
            apply_time_ms=response.apply_time_ms,
            error_message=response.error_message,
        )
    except grpc.RpcError as exc:
        return GrpcExecResult(
            node_id=node_id,
            pod_ip=pod_ip,
            success=False,
            entries_installed=0,
            apply_time_ms=0.0,
            error_message=str(exc),
        )
    except Exception as exc:
        return GrpcExecResult(
            node_id=node_id,
            pod_ip=pod_ip,
            success=False,
            entries_installed=0,
            apply_time_ms=0.0,
            error_message=str(exc),
        )


def push_to_nodes_grpc(
    push_tasks: list[tuple[str, str, ForwardingTableUpdate]],
    port: int | None = None,
    timeout: float | None = None,
    max_workers: int | None = None,
) -> list[GrpcExecResult]:
    """Push forwarding tables to multiple nodes in parallel via gRPC.

    push_tasks: list of (node_id, pod_ip, ForwardingTableUpdate) tuples.
    Returns results in the same order as input.
    """
    if not push_tasks:
        return []

    if port is None:
        port = _default_grpc_port()
    if timeout is None:
        timeout = _default_timeout()
    if max_workers is None:
        max_workers = _max_workers()

    results: list[GrpcExecResult | None] = [None] * len(push_tasks)

    def _push_indexed(index: int, node_id: str, pod_ip: str, update: ForwardingTableUpdate) -> None:
        results[index] = push_forwarding_table(node_id, pod_ip, update, port, timeout)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(push_tasks))) as pool:
        futures = []
        for i, (node_id, pod_ip, update) in enumerate(push_tasks):
            futures.append(pool.submit(_push_indexed, i, node_id, pod_ip, update))
        for f in futures:
            f.result()

    return results  # type: ignore[return-value]
