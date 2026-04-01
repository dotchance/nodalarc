"""Shared test fixtures for Nodal Arc.

Expanded incrementally as Steps 2-8 add new test needs.
"""

from pathlib import Path

import pytest
import zmq

# Path constants — tests load valid configs from configs/, not duplicated fixtures
PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def _init_platform_config():
    """Initialize PlatformConfig for all tests from standard values."""
    from nodalarc.platform import PlatformConfig, init_platform_config, reset_platform_config

    cfg = PlatformConfig(
        kubernetes_namespace="nodalarc",
        zmq_ome_events_port=5560,
        zmq_to_events_port=5561,
        zmq_mi_events_port=5562,
        zmq_mi_convergence_gate_port=5563,
        zmq_to_scenario_inject_port=5564,
        zmq_mi_trace_port=5565,
        zmq_playback_control_port=5566,
        zmq_nodalpath_events_port=5567,
        vs_api_http_port=8080,
        vf_static_file_server_port=8081,
        nodalpath_console_http_port=3100,
        nodalpath_fwd_grpc_port=50051,
        nodalpath_fwd_netconf_port=830,
        probe_daemon_http_api_port=9100,
        probe_daemon_udp_data_port=19100,
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
        nats_url="nats://localhost:4222",
    )
    init_platform_config(cfg)
    yield
    reset_platform_config()


@pytest.fixture
def zmq_context():
    """Provide a ZeroMQ context, cleaned up after use."""
    ctx = zmq.Context()
    yield ctx
    ctx.destroy(linger=0)
