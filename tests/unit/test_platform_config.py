"""Tests for PlatformConfig Pydantic model and singleton."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nodalarc.platform import (
    PlatformConfig,
    get_platform_config,
    init_platform_config,
    reset_platform_config,
)


def _valid_config_dict() -> dict:
    return {
        "kubernetes_namespace": "nodalarc",
        "zmq_ome_events_port": 5560,
        "zmq_to_events_port": 5561,
        "zmq_mi_events_port": 5562,
        "zmq_mi_convergence_gate_port": 5563,
        "zmq_to_scenario_inject_port": 5564,
        "zmq_mi_trace_port": 5565,
        "zmq_playback_control_port": 5566,
        "zmq_nodalpath_events_port": 5567,
        "vs_api_http_port": 8080,
        "vf_static_file_server_port": 8081,
        "nodalpath_console_http_port": 3100,
        "nodalpath_fwd_grpc_port": 50051,
        "nodalpath_fwd_netconf_port": 830,
        "probe_daemon_http_api_port": 9100,
        "probe_daemon_udp_data_port": 19100,
        "deploy_daemon_unix_socket_path": "/tmp/nodal-deploy.sock",
        "frr_config_directory_in_container": "/etc/frr",
        "frr_config_ready_sentinel_path": "/etc/frr/.config-ready",
        "veth_interface_mtu_bytes": 9000,
        "mpls_kernel_max_platform_labels": 100000,
        "pod_ready_timeout_seconds": 600,
        "pod_termination_timeout_seconds": 120,
        "deploy_operation_timeout_seconds": 600,
        "deploy_daemon_accept_timeout_seconds": 660,
        "frr_config_delivery_settle_seconds": 5,
        "kubectl_exec_max_parallel_workers": 20,
        "vs_api_max_websocket_connections": 50,
        "vs_api_introspect_max_requests_per_minute": 10,
        "vs_api_playback_max_requests_per_minute": 30,
        "vs_api_session_switch_max_requests_per_minute": 5,
        "vs_api_introspect_max_response_bytes": 65536,
        "vs_api_introspect_command_timeout_seconds": 15,
        "host_inotify_max_user_instances": 512,
        "host_file_descriptor_limit": 65536,
    }


class TestPlatformConfig:
    def test_validates_from_dict(self):
        cfg = PlatformConfig(**_valid_config_dict())
        assert cfg.kubernetes_namespace == "nodalarc"
        assert cfg.zmq_ome_events_port == 5560

    def test_frozen(self):
        cfg = PlatformConfig(**_valid_config_dict())
        with pytest.raises(ValidationError):
            cfg.kubernetes_namespace = "other"

    def test_missing_field_raises(self):
        d = _valid_config_dict()
        del d["kubernetes_namespace"]
        with pytest.raises(ValidationError):
            PlatformConfig(**d)

    def test_zmq_address_properties(self):
        cfg = PlatformConfig(**_valid_config_dict())
        assert cfg.ome_events_bind == "tcp://127.0.0.1:5560"
        assert cfg.ome_events_connect == "tcp://127.0.0.1:5560"
        assert cfg.to_events_bind == "tcp://127.0.0.1:5561"
        assert cfg.playback_control_bind == "tcp://127.0.0.1:5566"
        assert cfg.nodalpath_events_connect == "tcp://127.0.0.1:5567"


class TestSingleton:
    def setup_method(self):
        reset_platform_config()

    def teardown_method(self):
        # Re-initialize with standard values so other tests still work
        init_platform_config(PlatformConfig(**_valid_config_dict()))

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_platform_config()

    def test_init_from_object(self):
        cfg = PlatformConfig(**_valid_config_dict())
        result = init_platform_config(cfg)
        assert result is cfg
        assert get_platform_config() is cfg

    def test_init_from_yaml(self, tmp_path):
        import yaml

        yaml_path = tmp_path / "platform.yaml"
        yaml_path.write_text(yaml.dump({"platform": _valid_config_dict()}))
        result = init_platform_config(yaml_path)
        assert result.kubernetes_namespace == "nodalarc"
        assert get_platform_config() is result
