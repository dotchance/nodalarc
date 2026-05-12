"""Test that node_agent.proto exposes the Node Agent NATS payload contract."""

from __future__ import annotations


def test_proto_stubs_importable():
    """Generated pb2 module imports and exposes payload messages."""
    from nodalarc.proto import node_agent_pb2

    assert hasattr(node_agent_pb2, "CommandEnvelope")
    assert hasattr(node_agent_pb2, "CommandFailureResponse")
    assert hasattr(node_agent_pb2, "BatchLinkDownRequest")
    assert hasattr(node_agent_pb2, "BatchLinkUpRequest")
    assert hasattr(node_agent_pb2, "SetLatencyRequest")
    assert hasattr(node_agent_pb2, "InterfaceDown")
    assert hasattr(node_agent_pb2, "InterfaceUp")
    assert hasattr(node_agent_pb2, "InterfaceResult")
    assert hasattr(node_agent_pb2, "LatencyEntry")
    # GetTopology was removed per PRD v0.72 §1D (forbidden in IGP sessions).
    assert not hasattr(node_agent_pb2, "GetTopologyRequest")
    assert not hasattr(node_agent_pb2, "GetTopologyResponse")
    assert not hasattr(node_agent_pb2, "InterfaceState")
    assert "NodeAgentService" not in node_agent_pb2.DESCRIPTOR.services_by_name


def test_enum_values():
    """LinkType and Locality enums have expected values."""
    from nodalarc.proto import node_agent_pb2

    assert node_agent_pb2.LINK_TYPE_UNSPECIFIED == 0
    assert node_agent_pb2.LINK_TYPE_ISL == 1
    assert node_agent_pb2.LINK_TYPE_GROUND == 2
    assert node_agent_pb2.LOCALITY_UNSPECIFIED == 0
    assert node_agent_pb2.LOCALITY_LOCAL == 1
    assert node_agent_pb2.LOCALITY_CROSS_NODE == 2


def test_message_construction():
    """Proto messages can be constructed with fields."""
    from nodalarc.proto import node_agent_pb2

    down = node_agent_pb2.InterfaceDown(
        node_id="sat-p00s00",
        interface_name="isl0",
        link_type=node_agent_pb2.LINK_TYPE_ISL,
        locality=node_agent_pb2.LOCALITY_LOCAL,
        peer_node_id="sat-p00s01",
        peer_interface_name="isl1",
    )
    assert down.node_id == "sat-p00s00"
    assert down.interface_name == "isl0"
    assert down.link_type == node_agent_pb2.LINK_TYPE_ISL

    req = node_agent_pb2.BatchLinkDownRequest(
        envelope=node_agent_pb2.CommandEnvelope(
            operation_id="1710000000-down-001",
            session_id="demo",
            wiring_generation="sha256:" + "a" * 64,
            operation_kind="BatchLinkDown",
        ),
        target_sim_time="2024-03-09T18:00:00Z",
        interfaces=[down],
    )
    assert req.envelope.operation_id == "1710000000-down-001"
    assert len(req.interfaces) == 1
    assert req.interfaces[0].node_id == "sat-p00s00"
    assert req.interfaces[0].locality == node_agent_pb2.LOCALITY_LOCAL

    resp = node_agent_pb2.BatchLinkDownResponse(
        success=False,
        interfaces_downed=0,
        interface_results=[
            node_agent_pb2.InterfaceResult(
                node_id="sat-p00s00",
                interface_name="isl0",
                success=False,
                error_code=node_agent_pb2.NODE_AGENT_PID_NOT_FOUND,
                error_message="missing pid",
            )
        ],
    )
    assert resp.interface_results[0].node_id == "sat-p00s00"
    assert resp.interface_results[0].success is False


def test_latency_entry():
    """LatencyEntry carries all required fields."""
    from nodalarc.proto import node_agent_pb2

    entry = node_agent_pb2.LatencyEntry(
        node_id="sat-p00s00",
        interface_name="isl0",
        latency_ms=3.45,
        link_type=node_agent_pb2.LINK_TYPE_ISL,
    )
    assert entry.latency_ms == 3.45
