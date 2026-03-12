"""gRPC transport for pushing forwarding tables to nodalpath-fwd containers.

Each call creates a fresh channel, pushes a full table replacement, and closes.
No connection pooling — the push interval (~seconds) doesn't justify it.
"""

from __future__ import annotations

import logging
import time
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

log = logging.getLogger(__name__)

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
) -> ForwardingTableUpdate:
    """Convert a ForwardingTable to a gRPC ForwardingTableUpdate message.

    Always sends the full table — no deltas.
    Push actions are skipped (not supported in kernel MPLS).
    """
    lsr_entries: list[LabelEntry] = []
    for binding in table.lsr_bindings:
        if binding.action == "swap":
            entry = LabelEntry(
                in_label=binding.in_label,
                action=Action.SWAP,
                out_label=binding.out_label or 0,
                out_interface=binding.out_interface,
                backup_out_label=binding.backup_out_label or 0,
                backup_out_interface=binding.backup_out_interface or "",
            )
            lsr_entries.append(entry)
        elif binding.action == "pop":
            entry = LabelEntry(
                in_label=binding.in_label,
                action=Action.POP,
                out_label=0,
                out_interface=binding.out_interface,
                backup_out_label=binding.backup_out_label or 0,
                backup_out_interface=binding.backup_out_interface or "",
            )
            lsr_entries.append(entry)
        else:
            log.warning(
                "Skipping push action for in_label=%d on %s (not supported in kernel MPLS)",
                binding.in_label, table.node_id,
            )

    ler_entries: list[IngressEntry] = []
    for rule in table.ler_ingress_rules:
        entry = IngressEntry(
            dst_prefix=rule.dst_prefix,
            push_label=rule.push_label,
            out_interface=rule.out_interface,
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
    channel = grpc.insecure_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout)
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
    except grpc.FutureTimeoutError:
        return GrpcExecResult(
            node_id=node_id, pod_ip=pod_ip, success=False,
            entries_installed=0, apply_time_ms=0.0,
            error_message=f"Channel not ready after {timeout}s",
        )
    except grpc.RpcError as exc:
        return GrpcExecResult(
            node_id=node_id, pod_ip=pod_ip, success=False,
            entries_installed=0, apply_time_ms=0.0,
            error_message=str(exc),
        )
    except Exception as exc:
        return GrpcExecResult(
            node_id=node_id, pod_ip=pod_ip, success=False,
            entries_installed=0, apply_time_ms=0.0,
            error_message=str(exc),
        )
    finally:
        channel.close()


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
