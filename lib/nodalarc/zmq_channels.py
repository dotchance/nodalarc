"""ZeroMQ socket addresses, topic prefixes, and port accessors.

All ZeroMQ port numbers and topic strings live here. No component
invents its own port numbers or topic strings.

Port numbers and socket addresses are accessed via functions that read
from the PlatformConfig singleton. Topic bytes constants and
encode/decode remain as module-level constants.
"""

from nodalarc.platform import get_platform_config

# --- Port accessor functions ---


def ome_events_port() -> int:
    return get_platform_config().zmq_ome_events_port


def to_events_port() -> int:
    return get_platform_config().zmq_to_events_port


def mi_events_port() -> int:
    return get_platform_config().zmq_mi_events_port


def mi_convergence_gate_port() -> int:
    return get_platform_config().zmq_mi_convergence_gate_port


def to_scenario_inject_port() -> int:
    return get_platform_config().zmq_to_scenario_inject_port


def mi_trace_port() -> int:
    return get_platform_config().zmq_mi_trace_port


def playback_control_port() -> int:
    return get_platform_config().zmq_playback_control_port


def nodalpath_events_port() -> int:
    return get_platform_config().zmq_nodalpath_events_port


def vs_api_http_port() -> int:
    return get_platform_config().vs_api_http_port


def vf_static_port() -> int:
    return get_platform_config().vf_static_file_server_port


def probe_daemon_port() -> int:
    return get_platform_config().probe_daemon_http_api_port


def nodalpath_console_port() -> int:
    return get_platform_config().nodalpath_console_http_port


def nodalpath_fwd_grpc_port() -> int:
    return get_platform_config().nodalpath_fwd_grpc_port


def probe_daemon_udp_data_port() -> int:
    return get_platform_config().probe_daemon_udp_data_port


# --- Socket address accessor functions ---


def ome_events_bind() -> str:
    return get_platform_config().ome_events_bind


def ome_events_connect() -> str:
    return get_platform_config().ome_events_connect


def to_events_bind() -> str:
    return get_platform_config().to_events_bind


def to_events_connect() -> str:
    return get_platform_config().to_events_connect


def mi_events_bind() -> str:
    return get_platform_config().mi_events_bind


def mi_events_connect() -> str:
    return get_platform_config().mi_events_connect


def mi_convergence_gate_bind() -> str:
    return get_platform_config().mi_convergence_gate_bind


def mi_convergence_gate_connect() -> str:
    return get_platform_config().mi_convergence_gate_connect


def to_scenario_inject_bind() -> str:
    return get_platform_config().to_scenario_inject_bind


def to_scenario_inject_connect() -> str:
    return get_platform_config().to_scenario_inject_connect


def mi_trace_bind() -> str:
    return get_platform_config().mi_trace_bind


def mi_trace_connect() -> str:
    return get_platform_config().mi_trace_connect


def playback_control_bind() -> str:
    return get_platform_config().playback_control_bind


def playback_control_connect() -> str:
    return get_platform_config().playback_control_connect


def nodalpath_events_bind() -> str:
    return get_platform_config().nodalpath_events_bind


def nodalpath_events_connect() -> str:
    return get_platform_config().nodalpath_events_connect


# Topic prefixes (UTF-8 encoded, separated from payload by null byte)
TOPIC_POSITION_EVENT: bytes = b"PositionEvent"
TOPIC_VISIBILITY_EVENT: bytes = b"VisibilityEvent"
TOPIC_CLOCK_TICK: bytes = b"ClockTick"
TOPIC_LINK_UP: bytes = b"LinkUp"
TOPIC_LINK_DOWN: bytes = b"LinkDown"
TOPIC_LATENCY_UPDATE: bytes = b"LatencyUpdate"
TOPIC_CONVERGENCE_RESULT: bytes = b"ConvergenceResult"
TOPIC_PROBE_RESULT: bytes = b"ProbeResult"
TOPIC_ADAPTER_EVENT: bytes = b"AdapterEvent"
TOPIC_ALMANAC_EVENT: bytes = b"AlmanacEvent"

# Null byte separator
_NULL_SEP: bytes = b"\x00"


def encode_message(topic: bytes, json_payload: bytes) -> bytes:
    """Encode a ZeroMQ message: topic + null byte + JSON payload."""
    return topic + _NULL_SEP + json_payload


def decode_message(raw: bytes) -> tuple[bytes, bytes]:
    """Decode a ZeroMQ message into (topic, json_payload).

    Splits on the first null byte.
    """
    idx = raw.index(_NULL_SEP)
    return raw[:idx], raw[idx + 1 :]
