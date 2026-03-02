"""ZeroMQ socket addresses, topic prefixes, and port constants.

All ZeroMQ port numbers and topic strings live here. No component
invents its own port numbers or topic strings.
"""

# Port assignments
OME_EVENTS_PORT: int = 5560
TO_EVENTS_PORT: int = 5561
MI_EVENTS_PORT: int = 5562
MI_CONVERGENCE_GATE_PORT: int = 5563
TO_SCENARIO_INJECT_PORT: int = 5564
MI_TRACE_PORT: int = 5565
VS_API_HTTP_PORT: int = 8080
VF_STATIC_PORT: int = 8081
PROBE_DAEMON_PORT: int = 9100

# Socket addresses (for binding/connecting)
OME_EVENTS_BIND: str = f"tcp://*:{OME_EVENTS_PORT}"
OME_EVENTS_CONNECT: str = f"tcp://localhost:{OME_EVENTS_PORT}"

TO_EVENTS_BIND: str = f"tcp://*:{TO_EVENTS_PORT}"
TO_EVENTS_CONNECT: str = f"tcp://localhost:{TO_EVENTS_PORT}"

MI_EVENTS_BIND: str = f"tcp://*:{MI_EVENTS_PORT}"
MI_EVENTS_CONNECT: str = f"tcp://localhost:{MI_EVENTS_PORT}"

MI_CONVERGENCE_GATE_BIND: str = f"tcp://*:{MI_CONVERGENCE_GATE_PORT}"
MI_CONVERGENCE_GATE_CONNECT: str = f"tcp://localhost:{MI_CONVERGENCE_GATE_PORT}"

TO_SCENARIO_INJECT_BIND: str = f"tcp://*:{TO_SCENARIO_INJECT_PORT}"
TO_SCENARIO_INJECT_CONNECT: str = f"tcp://localhost:{TO_SCENARIO_INJECT_PORT}"

MI_TRACE_BIND: str = f"tcp://*:{MI_TRACE_PORT}"
MI_TRACE_CONNECT: str = f"tcp://localhost:{MI_TRACE_PORT}"

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
