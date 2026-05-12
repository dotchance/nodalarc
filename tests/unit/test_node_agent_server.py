# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Node Agent NATS request frame dispatch."""

from __future__ import annotations

from nodalarc.proto import node_agent_pb2
from node_agent.command_contract import RuntimeFence
from node_agent.server import dispatch

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64
FENCE = RuntimeFence(session_id=SESSION_ID, wiring_generation=WIRING_GENERATION)


def _frame(kind: bytes, payload) -> bytes:
    return kind + b"\x00" + payload.SerializeToString()


def _env(kind: str, generation: str = WIRING_GENERATION) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id=f"op-{kind}",
        session_id=SESSION_ID,
        wiring_generation=generation,
        operation_kind=kind,
    )


def test_malformed_frame_returns_structured_failure_response() -> None:
    raw = dispatch(b"not-a-frame", {}, FENCE)
    response = node_agent_pb2.CommandFailureResponse()
    response.ParseFromString(raw)

    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_MALFORMED_FRAME


def test_unknown_message_type_returns_structured_failure_response() -> None:
    raw = dispatch(b"Unknown\x00payload", {}, FENCE)
    response = node_agent_pb2.CommandFailureResponse()
    response.ParseFromString(raw)

    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_UNKNOWN_MESSAGE_TYPE


def test_bad_protobuf_returns_typed_operation_failure_response() -> None:
    raw = dispatch(b"BatchLinkUp\x00\xff", {}, FENCE)
    response = node_agent_pb2.BatchLinkUpResponse()
    response.ParseFromString(raw)

    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_BAD_PROTOBUF


def test_stale_envelope_is_rejected_before_mutation() -> None:
    request = node_agent_pb2.BatchLinkDownRequest(
        envelope=_env("BatchLinkDown", generation="sha256:" + "b" * 64)
    )

    raw = dispatch(_frame(b"BatchLinkDown", request), {}, FENCE)
    response = node_agent_pb2.BatchLinkDownResponse()
    response.ParseFromString(raw)

    assert response.success is False
    assert response.error_code == node_agent_pb2.NODE_AGENT_STALE_GENERATION


def test_valid_set_latency_request_dispatches_to_handler() -> None:
    request = node_agent_pb2.SetLatencyRequest(envelope=_env("SetLatency"))

    raw = dispatch(_frame(b"SetLatency", request), {}, FENCE)
    response = node_agent_pb2.SetLatencyResponse()
    response.ParseFromString(raw)

    assert response.success is True
    assert response.entries_updated == 0
