"""Shared test fixtures for Nodal Arc.

Expanded incrementally as Steps 2-8 add new test needs.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

# Path constants for local test assets.
PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def _init_platform_config():
    """Initialize PlatformConfig for all tests from standard values."""
    from nodalarc.platform_config import PlatformConfig, init_platform_config, reset_platform_config

    cfg = PlatformConfig(
        kubernetes_namespace="nodalarc",
        default_service_host="127.0.0.1",
        vs_api_http_port=8080,
        vf_static_file_server_port=8081,
        nodalpath_console_http_port=3100,
        nodalpath_fwd_grpc_port=50051,
        nodalpath_fwd_netconf_port=830,
        probe_daemon_http_api_port=9100,
        probe_daemon_udp_data_port=19100,
        deploy_daemon_unix_socket_path="/tmp/nodal-deploy.sock",
        session_data_root="/var/nodalarc/sessions",
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
        vs_api_visual_beam_falloff_exponent=2.0,
        vs_api_actuation_expected_latency_ms=250.0,
        vs_api_actuation_fault_after_ms=1200.0,
        scheduler_clean_kernel_audit_interval_s=60.0,
        default_session_pod_placement_policy="planePerNode",
        default_session_pod_planes_per_group=1,
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
        # Unit tests must not silently bind to a developer's live local NATS.
        nats_url="nats://unit-test-nats.invalid:4222",
    )
    init_platform_config(cfg)
    yield
    reset_platform_config()


@pytest.fixture(autouse=True)
def _node_agent_ops_spool_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep Node Agent pre-init OpsEvent spooling inside the test temp dir."""
    monkeypatch.setenv("NODE_AGENT_OPS_SPOOL", str(tmp_path / "node-agent-ops-events.jsonl"))


def load_runtime_segment_test_resolution(*, origin: str, name: str = "earth-leo-simple"):
    """Resolve the shipped runtime-complete catalog session used by unit tests."""
    from nodalarc.resolve_session import load_session_resolution_from_file

    session_path = PROJECT_ROOT / "catalog" / "nodalarc" / "sessions" / f"{name}.yaml"
    return load_session_resolution_from_file(session_path, origin=origin)


def load_runtime_ome_test_inputs(*, origin: str, name: str = "earth-leo-simple"):
    """Return OME's current resolved runtime inputs for a catalog test session."""
    from nodalarc.ome_inputs import build_ome_inputs_from_resolved

    resolution = load_runtime_segment_test_resolution(origin=origin, name=name)
    runtime = build_ome_inputs_from_resolved(resolution.resolved)
    if resolution.resolved.time is None:
        raise AssertionError("test session must include time")
    session_view = SimpleNamespace(
        time=SimpleNamespace(step_seconds=int(resolution.resolved.time.step_seconds)),
        orbit=SimpleNamespace(propagator=runtime.propagator_id),
        scheduling=SimpleNamespace(ground=runtime.ground_scheduling),
        ground_link_model=runtime.ground_link_model,
        node_metadata=runtime.node_metadata,
        body_frames=runtime.body_frames,
    )
    return (
        session_view,
        resolution.resolved,
        runtime.gs_file,
        runtime.satellites,
        runtime.addressing,
        runtime.neighbors,
        dict(runtime.ground_candidate_satellites_by_gs),
    )
