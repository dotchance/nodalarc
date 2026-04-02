"""Phase 1 test: node_agent.proto compiles and stubs are importable."""

from __future__ import annotations


def test_proto_stubs_importable():
    """Generated pb2 and pb2_grpc modules import without error."""
    from nodalarc.proto import node_agent_pb2, node_agent_pb2_grpc

    assert hasattr(node_agent_pb2, "BatchLinkDownRequest")
    assert hasattr(node_agent_pb2, "BatchLinkUpRequest")
    assert hasattr(node_agent_pb2, "SetLatencyRequest")
    assert hasattr(node_agent_pb2, "GetTopologyRequest")
    assert hasattr(node_agent_pb2, "InterfaceDown")
    assert hasattr(node_agent_pb2, "InterfaceUp")
    assert hasattr(node_agent_pb2, "LatencyEntry")
    assert hasattr(node_agent_pb2, "InterfaceState")
    assert hasattr(node_agent_pb2_grpc, "NodeAgentServiceServicer")
    assert hasattr(node_agent_pb2_grpc, "NodeAgentServiceStub")
    assert hasattr(node_agent_pb2_grpc, "add_NodeAgentServiceServicer_to_server")


def test_enum_values():
    """LinkType and Locality enums have expected values."""
    from nodalarc.proto import node_agent_pb2

    assert node_agent_pb2.ISL == 0
    assert node_agent_pb2.GROUND == 1
    assert node_agent_pb2.LOCAL == 0
    assert node_agent_pb2.CROSS_NODE == 1


def test_message_construction():
    """Proto messages can be constructed with fields."""
    from nodalarc.proto import node_agent_pb2

    down = node_agent_pb2.InterfaceDown(
        node_id="sat-p00s00",
        interface_name="isl0",
        link_type=node_agent_pb2.ISL,
    )
    assert down.node_id == "sat-p00s00"
    assert down.interface_name == "isl0"
    assert down.link_type == node_agent_pb2.ISL

    req = node_agent_pb2.BatchLinkDownRequest(
        batch_id="1710000000-down-001",
        target_sim_time="2024-03-09T18:00:00Z",
        locality=node_agent_pb2.LOCAL,
        interfaces=[down],
    )
    assert req.batch_id == "1710000000-down-001"
    assert len(req.interfaces) == 1
    assert req.interfaces[0].node_id == "sat-p00s00"


def test_latency_entry():
    """LatencyEntry carries all required fields."""
    from nodalarc.proto import node_agent_pb2

    entry = node_agent_pb2.LatencyEntry(
        node_id="sat-p00s00",
        interface_name="isl0",
        latency_ms=3.45,
        link_type=node_agent_pb2.ISL,
    )
    assert entry.latency_ms == 3.45


def test_get_topology_response():
    """GetTopologyResponse holds interface state list."""
    from nodalarc.proto import node_agent_pb2

    iface = node_agent_pb2.InterfaceState(
        node_id="sat-p00s00",
        interface_name="isl0",
        admin_up=True,
        oper_up=True,
        current_latency_ms=3.0,
    )
    resp = node_agent_pb2.GetTopologyResponse(interfaces=[iface])
    assert len(resp.interfaces) == 1
    assert resp.interfaces[0].admin_up is True


def test_node_agent_grpc_port():
    """Port accessor returns 50100 from platform config."""
    from nodalarc.platform import PlatformConfig, init_platform_config, reset_platform_config

    reset_platform_config()
    cfg = PlatformConfig(
        kubernetes_namespace="nodalarc",
        vs_api_http_port=8080,
        vf_static_file_server_port=8081,
        nodalpath_console_http_port=3100,
        nodalpath_fwd_grpc_port=50051,
        nodalpath_fwd_netconf_port=830,
        probe_daemon_http_api_port=9100,
        probe_daemon_udp_data_port=19100,
        node_agent_grpc_port=50100,
        deploy_daemon_unix_socket_path="/tmp/nodal-deploy.sock",
        frr_config_directory_in_container="/etc/frr",
        frr_config_ready_sentinel_path="/etc/frr/.config-ready",
        veth_interface_mtu_bytes=9000,
        mpls_kernel_max_platform_labels=100000,
        pod_ready_timeout_seconds=600,
        pod_termination_timeout_seconds=120,
        deploy_operation_timeout_seconds=600,
        deploy_daemon_accept_timeout_seconds=660,
        frr_config_delivery_settle_seconds=5,
        kubectl_exec_max_parallel_workers=20,
        vs_api_max_websocket_connections=50,
        vs_api_introspect_max_requests_per_minute=10,
        vs_api_playback_max_requests_per_minute=30,
        vs_api_session_switch_max_requests_per_minute=5,
        vs_api_introspect_max_response_bytes=65536,
        vs_api_introspect_command_timeout_seconds=15,
        trace_interval_seconds=3.0,
        trace_interval_fast_seconds=1.0,
        trace_fast_window_seconds=30.0,
        host_inotify_max_user_instances=512,
        host_file_descriptor_limit=65536,
    )
    init_platform_config(cfg)
    try:
        from nodalarc.platform import get_platform_config

        assert get_platform_config().node_agent_grpc_port == 50100
    finally:
        reset_platform_config()
