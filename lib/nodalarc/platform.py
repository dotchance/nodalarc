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

    # ZeroMQ ports
    zmq_ome_events_port: int
    zmq_to_events_port: int
    zmq_mi_events_port: int
    zmq_mi_convergence_gate_port: int
    zmq_to_scenario_inject_port: int
    zmq_mi_trace_port: int
    zmq_playback_control_port: int
    zmq_nodalpath_events_port: int

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

    # ZMQ networking — controls bind/connect addresses for inter-component
    # communication. Default "127.0.0.1" for single-host operation.
    # Set bind to "0.0.0.0" and connect to K8s Service DNS names when
    # components run in separate pods.
    zmq_bind_host: str = "127.0.0.1"
    zmq_connect_host: str = "127.0.0.1"
    # Per-service connect host overrides. Keys are service names (ome, orchestrator,
    # mi, nodalpath). Values are hostnames or IPs. Falls back to zmq_connect_host
    # if a service isn't in the dict. Adding new services in future phases is a
    # YAML change, not a schema change.
    zmq_connect_hosts: dict[str, str] = {}

    def zmq_connect_host_for(self, service: str) -> str:
        """Resolve connect host for a named service, falling back to global default."""
        return self.zmq_connect_hosts.get(service, self.zmq_connect_host)

    # --- ZMQ socket address properties ---

    @property
    def ome_events_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_ome_events_port}"

    @property
    def ome_events_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('ome')}:{self.zmq_ome_events_port}"

    @property
    def to_events_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_to_events_port}"

    @property
    def to_events_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('orchestrator')}:{self.zmq_to_events_port}"

    @property
    def mi_events_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_mi_events_port}"

    @property
    def mi_events_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('mi')}:{self.zmq_mi_events_port}"

    @property
    def mi_convergence_gate_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_mi_convergence_gate_port}"

    @property
    def mi_convergence_gate_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('mi')}:{self.zmq_mi_convergence_gate_port}"

    @property
    def to_scenario_inject_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_to_scenario_inject_port}"

    @property
    def to_scenario_inject_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('orchestrator')}:{self.zmq_to_scenario_inject_port}"

    @property
    def mi_trace_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_mi_trace_port}"

    @property
    def mi_trace_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('mi')}:{self.zmq_mi_trace_port}"

    @property
    def playback_control_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_playback_control_port}"

    @property
    def playback_control_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('orchestrator')}:{self.zmq_playback_control_port}"

    @property
    def nodalpath_events_bind(self) -> str:
        return f"tcp://{self.zmq_bind_host}:{self.zmq_nodalpath_events_port}"

    @property
    def nodalpath_events_connect(self) -> str:
        return f"tcp://{self.zmq_connect_host_for('nodalpath')}:{self.zmq_nodalpath_events_port}"


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
        raise RuntimeError(
            "PlatformConfig not initialized. Call init_platform_config() first."
        )
    return _config


def reset_platform_config() -> None:
    """Reset the singleton (for tests only)."""
    global _config
    _config = None
