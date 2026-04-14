# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent NATS request dispatch.

Parses binary NATS request messages (type\0payload) and dispatches to
the appropriate handler. The event loop and NATS connection are owned
by __main__.py — this module provides the dispatch function only.
"""

from __future__ import annotations

import logging

from nodalarc.proto import node_agent_pb2

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)

log = logging.getLogger(__name__)


def dispatch(data: bytes, pid_map: dict[str, int]) -> bytes:
    """Dispatch a NATS request to the appropriate handler.

    Message format: type\0payload (null-separated ASCII type + protobuf body).
    Runs in a thread pool executor — handlers use concurrent.futures
    internally for batch parallelism.

    pid_map must be populated before dispatch is called (wiring gate).
    """
    sep = data.find(b"\x00")
    if sep < 0:
        log.warning("Malformed NATS message: no type separator")
        return b""

    msg_type = data[:sep]
    payload = data[sep + 1 :]

    if msg_type == b"BatchLinkDown":
        request = node_agent_pb2.BatchLinkDownRequest()
        request.ParseFromString(payload)
        response = handle_batch_link_down(request, context=None, pid_map=pid_map)
        return response.SerializeToString()

    elif msg_type == b"BatchLinkUp":
        request = node_agent_pb2.BatchLinkUpRequest()
        request.ParseFromString(payload)
        response = handle_batch_link_up(request, context=None, pid_map=pid_map)
        return response.SerializeToString()

    elif msg_type == b"SetLatency":
        request = node_agent_pb2.SetLatencyRequest()
        request.ParseFromString(payload)
        response = handle_set_latency(request, context=None, pid_map=pid_map)
        return response.SerializeToString()

    elif msg_type == b"GetTopology":
        request = node_agent_pb2.GetTopologyRequest()
        request.ParseFromString(payload)
        response = handle_get_topology(request, context=None, pid_map=pid_map)
        return response.SerializeToString()

    else:
        log.warning("Unknown message type: %s", msg_type)
        return b""
