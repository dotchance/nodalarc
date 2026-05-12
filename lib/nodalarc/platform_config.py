# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Platform configuration — single source of truth for deployment-level settings.

Loads from configs/platform.yaml. No fallback defaults in Python code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class PlatformConfig(BaseModel):
    """Frozen Pydantic model for platform configuration.

    All fields are required — no defaults. The YAML file is the single
    source of truth. If a field is missing, Pydantic raises ValidationError.
    """

    model_config = ConfigDict(frozen=True)

    # Kubernetes
    kubernetes_namespace: str

    # NATS JetStream
    nats_url: str = "nats://nodalarc-nats:4222"
    ome_link_state_snapshot_interval_s: float = 5.0

    # HTTP/WebSocket service ports
    vs_api_http_port: int
    vf_static_file_server_port: int
    nodalpath_console_http_port: int

    # Container-internal service ports
    nodalpath_fwd_grpc_port: int
    nodalpath_fwd_netconf_port: int
    probe_daemon_http_api_port: int
    probe_daemon_udp_data_port: int

    # Deploy daemon
    deploy_daemon_unix_socket_path: str

    # Container filesystem paths
    frr_config_directory_in_container: str
    frr_config_ready_sentinel_path: str

    # Network infrastructure
    veth_interface_mtu_bytes: int
    mpls_kernel_max_platform_labels: int

    # Operational timeouts (seconds)
    pod_ready_timeout_seconds: int
    pod_termination_timeout_seconds: int
    deploy_operation_timeout_seconds: int
    deploy_daemon_accept_timeout_seconds: int
    frr_config_delivery_settle_seconds: int

    # Parallel execution
    kubectl_exec_max_parallel_workers: int

    # VS-API operational limits
    vs_api_max_websocket_connections: int
    vs_api_introspect_max_requests_per_minute: int
    vs_api_playback_max_requests_per_minute: int
    vs_api_session_switch_max_requests_per_minute: int
    vs_api_introspect_max_response_bytes: int
    vs_api_introspect_command_timeout_seconds: int

    # Continuous trace intervals
    trace_interval_seconds: float
    trace_interval_fast_seconds: float
    trace_fast_window_seconds: float

    # System tuning
    host_inotify_max_user_instances: int
    host_file_descriptor_limit: int

    # Service host resolution — for inter-service HTTP calls (not NATS).
    # Keys: service names (vs-api, nodalpath, etc.). Values: hostnames.
    # Falls back to default_service_host if service not in dict.
    default_service_host: str
    service_hosts: dict[str, str] = {}

    def service_host(self, service: str) -> str:
        """Resolve hostname for a named service, falling back to default."""
        return self.service_hosts.get(service, self.default_service_host)


# --- Module-level singleton ---

_config: PlatformConfig | None = None


def init_platform_config(source: Path | PlatformConfig) -> PlatformConfig:
    """Initialize the platform config singleton.

    Args:
        source: Path to platform.yaml or a pre-built PlatformConfig (for tests).

    Returns:
        The initialized PlatformConfig.
    """
    global _config
    if isinstance(source, PlatformConfig):
        _config = source
    else:
        raw = yaml.safe_load(source.read_text())
        _config = PlatformConfig.model_validate(raw["platform"])
    return _config


def get_platform_config() -> PlatformConfig:
    """Return the platform config singleton.

    Raises RuntimeError if init_platform_config() has not been called.
    """
    if _config is None:
        raise RuntimeError("PlatformConfig not initialized. Call init_platform_config() first.")
    return _config


def reset_platform_config() -> None:
    """Reset the singleton (for tests only)."""
    global _config
    _config = None
