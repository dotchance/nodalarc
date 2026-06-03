"""Shared test fixtures for Nodal Arc.

Expanded incrementally as Steps 2-8 add new test needs.
"""

from pathlib import Path

import pytest

# Path constants — tests load valid configs from configs/, not duplicated fixtures
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


def build_segment_session_dict(
    *,
    name: str,
    constellation,
    ground_stations,
    data_dir: str | None = None,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    orbit_propagator: str = "keplerian-circular",
    routing: dict | None = None,
    scheduling: dict | None = None,
    time: dict | None = None,
    candidate_limit: int = 100000,
) -> dict:
    """Build a segment-grammar session for product/runtime tests."""
    routing_data = {
        "protocol": protocol,
        "extensions": list(extensions or []),
        "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
    }
    if routing:
        routing_data.update(routing)
    scheduling_data = scheduling or {
        "ground": {
            "selection_policy": {"name": "highest-elevation", "params": {}},
            "handover_policy": {
                "name": "hysteresis",
                "params": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0},
            },
            "handover_mode": "bbm",
            "mbb_overlap_ticks": 0,
            "mbb_reserve": 0,
        }
    }
    session_data = {"name": name}
    if data_dir is not None:
        session_data["data_dir"] = data_dir
    data = {
        "session": session_data,
        "identity": {"mode": "segment_namespaced"},
        "segments": [
            {
                "id": "space",
                "kind": "constellation",
                "source": constellation,
                "namespace": "space",
                "central_body": "earth",
            },
            {
                "id": "ground",
                "kind": "ground_set",
                "source": ground_stations,
                "namespace": "ground",
                "reference_body": "earth",
            },
        ],
        "link_rules": [
            {
                "id": "ground-access",
                "kind": "access",
                "endpoints": [
                    {"selector": {"segment": "ground"}, "terminal_role": "ground"},
                    {"selector": {"segment": "space"}, "terminal_role": "ground"},
                ],
                "topology": {"mode": "visible_candidates"},
            }
        ],
        "simulation": {"candidate_limits": {"max_pairs_per_rule": candidate_limit}},
        "orbit": {"propagator": orbit_propagator},
        "scheduling": scheduling_data,
        "routing": routing_data,
    }
    if time is not None:
        data["time"] = time
    return data
