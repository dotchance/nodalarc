# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Node Agent command contract validation.

The Node Agent is a privileged substrate actuator. Validation here is the
fence between untrusted request bytes and kernel mutation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from nodalarc.proto import node_agent_pb2

KIND_BATCH_LINK_DOWN = "BatchLinkDown"
KIND_BATCH_LINK_UP = "BatchLinkUp"
KIND_SET_LATENCY = "SetLatency"
KIND_KERNEL_INVENTORY = "KernelInventory"


@dataclass(frozen=True)
class RuntimeFence:
    session_id: str
    wiring_generation: str


class CommandContractError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_envelope(request, *, expected_kind: str, fence: RuntimeFence) -> None:
    if not fence.session_id:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
            "Node Agent has no current session_id; wiring is not ready",
        )
    if not fence.wiring_generation:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
            "Node Agent has no current wiring_generation; wiring is not ready",
        )
    if not request.HasField("envelope"):
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
            "missing command envelope",
        )

    env = request.envelope
    missing = [
        field
        for field in ("operation_id", "session_id", "wiring_generation", "operation_kind")
        if not getattr(env, field)
    ]
    if missing:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
            f"command envelope missing required field(s): {', '.join(missing)}",
        )
    if env.operation_kind != expected_kind:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
            f"command envelope kind {env.operation_kind!r} does not match {expected_kind!r}",
        )
    if env.session_id != fence.session_id:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_STALE_SESSION,
            f"stale session_id {env.session_id!r}; current session_id is {fence.session_id!r}",
        )
    if env.wiring_generation != fence.wiring_generation:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_STALE_GENERATION,
            "stale wiring_generation "
            f"{env.wiring_generation!r}; current generation is {fence.wiring_generation!r}",
        )


def _require_nonempty(value: str, field: str) -> None:
    if not value:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_FIELD,
            f"missing required field {field}",
        )


def _validate_link_type(value: int) -> None:
    if value == node_agent_pb2.LINK_TYPE_UNSPECIFIED:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_FIELD,
            "link_type must not be LINK_TYPE_UNSPECIFIED",
        )


def _validate_locality(value: int) -> None:
    if value == node_agent_pb2.LOCALITY_UNSPECIFIED:
        raise CommandContractError(
            node_agent_pb2.NODE_AGENT_INVALID_FIELD,
            "locality must not be LOCALITY_UNSPECIFIED",
        )


def _validate_common_interface(iface, *, operation: str) -> None:
    _require_nonempty(iface.node_id, "node_id")
    _require_nonempty(iface.interface_name, "interface_name")
    _validate_link_type(iface.link_type)
    _validate_locality(iface.locality)

    if iface.link_type == node_agent_pb2.LINK_TYPE_GROUND:
        _require_nonempty(iface.gs_id, "gs_id")
        _require_nonempty(iface.sat_id, "sat_id")
        _require_nonempty(iface.peer_node_id, "peer_node_id")
        _require_nonempty(iface.peer_interface_name, "peer_interface_name")
    else:
        _require_nonempty(iface.peer_node_id, "peer_node_id")
        _require_nonempty(iface.peer_interface_name, "peer_interface_name")

    if iface.locality == node_agent_pb2.LOCALITY_CROSS_NODE:
        _require_nonempty(iface.remote_node_ip, "remote_node_ip")
        if iface.vni <= 0:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"{operation} CROSS_NODE entry requires vni > 0",
            )


def validate_batch_link_up_request(request, *, fence: RuntimeFence) -> None:
    validate_envelope(request, expected_kind=KIND_BATCH_LINK_UP, fence=fence)
    for iface in request.interfaces:
        _validate_common_interface(iface, operation=KIND_BATCH_LINK_UP)
        if iface.latency_ms < 0:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                "latency_ms must be >= 0",
            )
        if iface.bandwidth_mbps <= 0:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                "bandwidth_mbps must be > 0",
            )


def validate_batch_link_down_request(request, *, fence: RuntimeFence) -> None:
    validate_envelope(request, expected_kind=KIND_BATCH_LINK_DOWN, fence=fence)
    for iface in request.interfaces:
        _validate_common_interface(iface, operation=KIND_BATCH_LINK_DOWN)


def validate_set_latency_request(request, *, fence: RuntimeFence) -> None:
    validate_envelope(request, expected_kind=KIND_SET_LATENCY, fence=fence)
    for entry in request.entries:
        _require_nonempty(entry.node_id, "node_id")
        _require_nonempty(entry.interface_name, "interface_name")
        _validate_link_type(entry.link_type)
        if entry.latency_ms < 0:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                "latency_ms must be >= 0",
            )
        if entry.link_type == node_agent_pb2.LINK_TYPE_GROUND:
            _require_nonempty(entry.gs_id, "gs_id")
            _require_nonempty(entry.sat_id, "sat_id")


def validate_kernel_inventory_request(request, *, fence: RuntimeFence) -> None:
    validate_envelope(request, expected_kind=KIND_KERNEL_INVENTORY, fence=fence)
    _require_nonempty(request.gs_id, "gs_id")
    for entry in request.entries:
        _require_nonempty(entry.node_id, "node_id")
        _require_nonempty(entry.interface_name, "interface_name")
        _validate_link_type(entry.link_type)
        _validate_locality(entry.locality)
        if entry.link_type != node_agent_pb2.LINK_TYPE_GROUND:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                "KernelInventory in Phase 5 supports ground entries only",
            )
        _require_nonempty(entry.gs_id, "gs_id")
        _require_nonempty(entry.sat_id, "sat_id")
        _require_nonempty(entry.peer_node_id, "peer_node_id")
        _require_nonempty(entry.peer_interface_name, "peer_interface_name")
        if entry.gs_id != request.gs_id:
            raise CommandContractError(
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                f"entry gs_id {entry.gs_id!r} does not match request gs_id {request.gs_id!r}",
            )
        if entry.expected_admin_up:
            if entry.latency_ms < 0:
                raise CommandContractError(
                    node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                    "latency_ms must be >= 0 for expected-up verification",
                )
            if entry.bandwidth_mbps <= 0:
                raise CommandContractError(
                    node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                    "bandwidth_mbps must be > 0 for expected-up verification",
                )
        if entry.locality == node_agent_pb2.LOCALITY_CROSS_NODE:
            if entry.vni <= 0:
                raise CommandContractError(
                    node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                    "KernelInventory CROSS_NODE entry requires vni > 0",
                )
            _require_nonempty(entry.remote_node_ip, "remote_node_ip")


def envelope(
    *,
    operation_id: str,
    session_id: str,
    wiring_generation: str,
    operation_kind: str,
) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id=operation_id,
        session_id=session_id,
        wiring_generation=wiring_generation,
        operation_kind=operation_kind,
    )


def worst_error_code(codes: Iterable[int]) -> int:
    non_ok = [code for code in codes if code != node_agent_pb2.NODE_AGENT_OK]
    return max(non_ok) if non_ok else node_agent_pb2.NODE_AGENT_OK
