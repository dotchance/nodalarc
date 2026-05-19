# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Node Agent NATS request dispatch.

Parses binary NATS request messages (type\0payload) and dispatches to
the appropriate handler. The event loop and NATS connection are owned
by __main__.py — this module provides the dispatch function only.
"""

from __future__ import annotations

import logging

from google.protobuf.message import DecodeError
from nodalarc.proto import node_agent_pb2

from node_agent import ops_events
from node_agent.command_contract import RuntimeFence
from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_set_latency,
)

log = logging.getLogger(__name__)


def _publish_transport_event(
    *,
    code: str,
    message: str,
    fence: RuntimeFence,
    dirty_kernel: bool = False,
) -> None:
    ops_events.publish(
        level="critical" if dirty_kernel else "warning",
        code=code,
        message=message,
        session_id=fence.session_id,
        details={
            "wiring_generation": fence.wiring_generation,
            "dirty_kernel": dirty_kernel,
        },
    )


def _failure(code: int, message: str, *, dirty_kernel: bool = False) -> bytes:
    return node_agent_pb2.CommandFailureResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
    ).SerializeToString()


def _batch_down_failure(code: int, message: str, *, dirty_kernel: bool = False) -> bytes:
    return node_agent_pb2.BatchLinkDownResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
    ).SerializeToString()


def _batch_up_failure(code: int, message: str, *, dirty_kernel: bool = False) -> bytes:
    return node_agent_pb2.BatchLinkUpResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
    ).SerializeToString()


def _set_latency_failure(code: int, message: str, *, dirty_kernel: bool = False) -> bytes:
    return node_agent_pb2.SetLatencyResponse(
        success=False,
        error_code=code,
        error_message=message,
        dirty_kernel=dirty_kernel,
    ).SerializeToString()


def dispatch(data: bytes, pid_map: dict[str, int], fence: RuntimeFence) -> bytes:
    """Dispatch a NATS request to the appropriate handler.

    Message format: type\0payload (null-separated ASCII type + protobuf body).
    Runs in a thread pool executor — handlers use concurrent.futures
    internally for batch parallelism.

    pid_map must be populated before dispatch is called (wiring gate).
    """
    sep = data.find(b"\x00")
    if sep < 0:
        log.warning("Malformed NATS message: no type separator")
        _publish_transport_event(
            code="COMMAND_REJECTED",
            message="Malformed Node Agent command frame: missing type separator",
            fence=fence,
        )
        return _failure(node_agent_pb2.NODE_AGENT_MALFORMED_FRAME, "missing type separator")

    msg_type = data[:sep]
    payload = data[sep + 1 :]

    if msg_type == b"BatchLinkDown":
        try:
            request = node_agent_pb2.BatchLinkDownRequest()
            request.ParseFromString(payload)
            response = handle_batch_link_down(request, context=None, pid_map=pid_map, fence=fence)
            return response.SerializeToString()
        except DecodeError as exc:
            log.warning("Bad protobuf for %s: %s", msg_type, exc)
            _publish_transport_event(
                code="COMMAND_REJECTED",
                message=f"Bad protobuf for BatchLinkDown: {exc}",
                fence=fence,
            )
            return _batch_down_failure(
                node_agent_pb2.NODE_AGENT_BAD_PROTOBUF, f"bad protobuf: {exc}"
            )
        except Exception as exc:
            log.error("Node Agent dispatch failed for %s: %s", msg_type, exc, exc_info=True)
            _publish_transport_event(
                code="DIRTY_KERNEL",
                message=f"BatchLinkDown dispatch failed: {exc}",
                fence=fence,
                dirty_kernel=True,
            )
            return _batch_down_failure(
                node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
                f"dispatch failed: {exc}",
                dirty_kernel=True,
            )

    if msg_type == b"BatchLinkUp":
        try:
            request = node_agent_pb2.BatchLinkUpRequest()
            request.ParseFromString(payload)
            response = handle_batch_link_up(request, context=None, pid_map=pid_map, fence=fence)
            return response.SerializeToString()
        except DecodeError as exc:
            log.warning("Bad protobuf for %s: %s", msg_type, exc)
            _publish_transport_event(
                code="COMMAND_REJECTED",
                message=f"Bad protobuf for BatchLinkUp: {exc}",
                fence=fence,
            )
            return _batch_up_failure(node_agent_pb2.NODE_AGENT_BAD_PROTOBUF, f"bad protobuf: {exc}")
        except Exception as exc:
            log.error("Node Agent dispatch failed for %s: %s", msg_type, exc, exc_info=True)
            _publish_transport_event(
                code="DIRTY_KERNEL",
                message=f"BatchLinkUp dispatch failed: {exc}",
                fence=fence,
                dirty_kernel=True,
            )
            return _batch_up_failure(
                node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
                f"dispatch failed: {exc}",
                dirty_kernel=True,
            )

    if msg_type == b"SetLatency":
        try:
            request = node_agent_pb2.SetLatencyRequest()
            request.ParseFromString(payload)
            response = handle_set_latency(request, context=None, pid_map=pid_map, fence=fence)
            return response.SerializeToString()
        except DecodeError as exc:
            log.warning("Bad protobuf for %s: %s", msg_type, exc)
            _publish_transport_event(
                code="COMMAND_REJECTED",
                message=f"Bad protobuf for SetLatency: {exc}",
                fence=fence,
            )
            return _set_latency_failure(
                node_agent_pb2.NODE_AGENT_BAD_PROTOBUF, f"bad protobuf: {exc}"
            )
        except Exception as exc:
            log.error("Node Agent dispatch failed for %s: %s", msg_type, exc, exc_info=True)
            _publish_transport_event(
                code="DIRTY_KERNEL",
                message=f"SetLatency dispatch failed: {exc}",
                fence=fence,
                dirty_kernel=True,
            )
            return _set_latency_failure(
                node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
                f"dispatch failed: {exc}",
                dirty_kernel=True,
            )

    log.warning("Unknown message type: %s", msg_type)
    _publish_transport_event(
        code="COMMAND_REJECTED",
        message=f"Unknown Node Agent command type: {msg_type.decode(errors='replace')}",
        fence=fence,
    )
    return _failure(
        node_agent_pb2.NODE_AGENT_UNKNOWN_MESSAGE_TYPE,
        f"unknown message type: {msg_type.decode(errors='replace')}",
    )
