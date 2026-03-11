"""gRPC transport for interrogating node forwarding state.

Mirrors grpc_push.py pattern: fresh channel per call, never raises,
ThreadPoolExecutor fan-out for parallel interrogation.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import grpc

from nodalpath.models.almanac import ForwardingTable
from nodalpath.models.inspection import (
    BindingDiff,
    BindingDiffKind,
    IngressDiff,
    NodeInspectionResult,
)
from nodalpath.proto import Action, Empty, ForwardingTableState, NodeStatus
from nodalpath.proto.forwarding_pb2_grpc import ForwardingServiceStub

log = logging.getLogger(__name__)

DEFAULT_GRPC_PORT: int = 50051
DEFAULT_TIMEOUT: float = 10.0
MAX_WORKERS: int = 20

_ACTION_MAP = {
    Action.SWAP: "swap",
    Action.POP: "pop",
    Action.PUSH: "push",
}


def _diff_lsr(
    planned: ForwardingTable,
    observed: ForwardingTableState,
) -> list[BindingDiff]:
    """Compare planned LSR bindings against observed LabelEntries."""
    planned_by_label = {b.in_label: b for b in planned.lsr_bindings}
    observed_by_label = {e.in_label: e for e in observed.lsr_entries}

    diffs: list[BindingDiff] = []

    for in_label, binding in planned_by_label.items():
        if binding.action == "push":
            continue
        if in_label not in observed_by_label:
            diffs.append(BindingDiff(
                in_label=in_label,
                kind=BindingDiffKind.MISSING,
                planned_action=binding.action,
                planned_out_label=binding.out_label,
                planned_out_interface=binding.out_interface,
            ))
        else:
            obs = observed_by_label[in_label]
            obs_action = _ACTION_MAP.get(obs.action, str(obs.action))
            obs_out_label = obs.out_label if obs.action != Action.POP else None
            planned_out = binding.out_label if binding.action != "pop" else None
            if (
                obs_action != binding.action
                or obs_out_label != planned_out
                or obs.out_interface != binding.out_interface
            ):
                diffs.append(BindingDiff(
                    in_label=in_label,
                    kind=BindingDiffKind.MISMATCH,
                    planned_action=binding.action,
                    planned_out_label=planned_out,
                    planned_out_interface=binding.out_interface,
                    observed_action=obs_action,
                    observed_out_label=obs_out_label,
                    observed_out_interface=obs.out_interface,
                ))

    for in_label, obs in observed_by_label.items():
        if in_label not in planned_by_label:
            obs_action = _ACTION_MAP.get(obs.action, str(obs.action))
            diffs.append(BindingDiff(
                in_label=in_label,
                kind=BindingDiffKind.EXTRA,
                observed_action=obs_action,
                observed_out_label=obs.out_label if obs.action != Action.POP else None,
                observed_out_interface=obs.out_interface,
            ))

    return diffs


def _diff_ingress(
    planned: ForwardingTable,
    observed: ForwardingTableState,
) -> list[IngressDiff]:
    """Compare planned ingress rules against observed IngressEntries."""
    planned_by_prefix = {r.dst_prefix: r for r in planned.ler_ingress_rules}
    observed_by_prefix = {e.dst_prefix: e for e in observed.ler_entries}

    diffs: list[IngressDiff] = []

    for prefix, rule in planned_by_prefix.items():
        if prefix not in observed_by_prefix:
            diffs.append(IngressDiff(
                dst_prefix=prefix,
                kind=BindingDiffKind.MISSING,
                planned_push_label=rule.push_label,
                planned_out_interface=rule.out_interface,
            ))
        else:
            obs = observed_by_prefix[prefix]
            if (
                obs.push_label != rule.push_label
                or obs.out_interface != rule.out_interface
            ):
                diffs.append(IngressDiff(
                    dst_prefix=prefix,
                    kind=BindingDiffKind.MISMATCH,
                    planned_push_label=rule.push_label,
                    planned_out_interface=rule.out_interface,
                    observed_push_label=obs.push_label,
                    observed_out_interface=obs.out_interface,
                ))

    for prefix, obs in observed_by_prefix.items():
        if prefix not in planned_by_prefix:
            diffs.append(IngressDiff(
                dst_prefix=prefix,
                kind=BindingDiffKind.EXTRA,
                observed_push_label=obs.push_label,
                observed_out_interface=obs.out_interface,
            ))

    return diffs


def interrogate_node(
    node_id: str,
    pod_ip: str,
    expected_topology_state_id: str,
    planned_table: ForwardingTable,
    port: int = DEFAULT_GRPC_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> NodeInspectionResult:
    """Interrogate a single node's forwarding state via gRPC.

    Never raises. All errors are captured in NodeInspectionResult.
    """
    target = f"{pod_ip}:{port}"
    channel = grpc.insecure_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout)
        stub = ForwardingServiceStub(channel)

        status: NodeStatus = stub.GetStatus(Empty(), timeout=timeout)
        fwd_state: ForwardingTableState = stub.GetForwardingTable(Empty(), timeout=timeout)

        binding_diffs = _diff_lsr(planned_table, fwd_state)
        ingress_diffs = _diff_ingress(planned_table, fwd_state)

        return NodeInspectionResult(
            node_id=node_id,
            reachable=True,
            status_topology_state_id=status.current_topology_state_id,
            status_total_entries=status.total_entries,
            binding_diffs=binding_diffs,
            ingress_diffs=ingress_diffs,
        )
    except grpc.FutureTimeoutError:
        return NodeInspectionResult(
            node_id=node_id,
            reachable=False,
            error_message=f"Channel not ready after {timeout}s",
        )
    except grpc.RpcError as exc:
        return NodeInspectionResult(
            node_id=node_id,
            reachable=False,
            error_message=str(exc),
        )
    except Exception as exc:
        return NodeInspectionResult(
            node_id=node_id,
            reachable=False,
            error_message=str(exc),
        )
    finally:
        channel.close()


def interrogate_nodes(
    tasks: list[tuple[str, str, str, ForwardingTable]],
    port: int = DEFAULT_GRPC_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = MAX_WORKERS,
) -> list[NodeInspectionResult]:
    """Interrogate multiple nodes in parallel via gRPC.

    tasks: list of (node_id, pod_ip, expected_topology_state_id, planned_table).
    Returns results in the same order as input.
    """
    if not tasks:
        return []

    results: list[NodeInspectionResult | None] = [None] * len(tasks)

    def _interrogate_indexed(
        index: int, node_id: str, pod_ip: str,
        expected_state_id: str, planned_table: ForwardingTable,
    ) -> None:
        results[index] = interrogate_node(
            node_id, pod_ip, expected_state_id, planned_table, port, timeout,
        )

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as pool:
        futures = []
        for i, (node_id, pod_ip, state_id, table) in enumerate(tasks):
            futures.append(pool.submit(
                _interrogate_indexed, i, node_id, pod_ip, state_id, table,
            ))
        for f in futures:
            f.result()

    return results  # type: ignore[return-value]
