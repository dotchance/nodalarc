# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Platform configuration — single source of truth for deployment-level settings.

Loads from configs/platform.yaml. No fallback defaults in Python code.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlatformConfig(BaseModel):
    """Frozen Pydantic model for platform configuration.

    Every field here is read by a service or a script; a key the model does
    not declare is refused, and a declared key that is missing from the file
    fails validation. The YAML file is the single source of truth.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Kubernetes
    kubernetes_namespace: str

    # NATS JetStream
    ome_link_state_snapshot_interval_s: float = Field(gt=0)

    # HTTP/WebSocket service ports
    vs_api_http_port: int
    nodalpath_console_http_port: int

    # Container-internal service ports
    nodalpath_fwd_grpc_port: int
    probe_daemon_http_api_port: int
    probe_daemon_udp_data_port: int

    session_data_root: str

    veth_interface_mtu_bytes: int

    vs_api_visual_beam_falloff_exponent: float = Field(gt=0)
    vs_api_actuation_expected_latency_ms: float = Field(gt=0)
    vs_api_actuation_fault_after_ms: float = Field(gt=0)
    scheduler_clean_kernel_audit_interval_s: float = Field(gt=0)
    default_session_pod_placement_policy: Literal["allOnOne", "planePerNode", "planeGroupPerNode"]
    default_session_pod_planes_per_group: int = Field(gt=0)
    vs_api_introspect_max_requests_per_minute: int
    vs_api_playback_max_requests_per_minute: int
    vs_api_session_switch_max_requests_per_minute: int
    vs_api_introspect_max_response_bytes: int

    # Continuous trace intervals
    trace_interval_seconds: float
    trace_interval_fast_seconds: float
    trace_fast_window_seconds: float

    # Service host resolution — for inter-service HTTP calls (not NATS).
    # Keys: service names (vs-api, nodalpath, etc.). Values: hostnames.
    # Falls back to default_service_host if service not in dict.
    default_service_host: str
    service_hosts: dict[str, str] = {}

    def service_host(self, service: str) -> str:
        """Resolve hostname for a named service, falling back to default."""
        return self.service_hosts.get(service, self.default_service_host)

    @model_validator(mode="after")
    def _validate_actuation_bounds(self) -> PlatformConfig:
        if self.vs_api_actuation_fault_after_ms <= self.vs_api_actuation_expected_latency_ms:
            raise ValueError(
                "vs_api_actuation_fault_after_ms must exceed vs_api_actuation_expected_latency_ms"
            )
        return self


def _deterministic_node(node_id: str, available_nodes: list[str]) -> str:
    best_node = available_nodes[0]
    best_weight = -1
    for node in available_nodes:
        weight = int(hashlib.sha256(f"{node_id}:{node}".encode()).hexdigest()[:8], 16)
        if weight > best_weight:
            best_weight = weight
            best_node = node
    return best_node


def compute_pod_placement(
    placement: Any,
    pod_inventory: dict[str, dict],
    available_nodes: list[str],
) -> dict[str, str]:
    """Compute Kubernetes node placement from the platform-owned policy."""
    if not available_nodes:
        raise ValueError("No available K3s nodes for pod placement")

    if isinstance(placement, Mapping):
        policy = str(placement.get("policy") or "")
        planes_per_group_value = placement.get("planes_per_group")
        if policy == "planeGroupPerNode" and planes_per_group_value is None:
            raise ValueError("planeGroupPerNode requires planes_per_group")
        planes_per_group = int(planes_per_group_value or 1)
    else:
        policy = str(getattr(placement, "policy", placement))
        planes_per_group = int(getattr(placement, "planes_per_group", 1) or 1)

    if policy == "allOnOne":
        target = available_nodes[0]
        return dict.fromkeys(pod_inventory, target)

    if policy == "planePerNode":
        result: dict[str, str] = {}
        for node_id, facts in pod_inventory.items():
            if facts.get("node_type") == "ground_station":
                result[node_id] = _deterministic_node(node_id, available_nodes)
            else:
                plane = facts.get("plane", 0)
                result[node_id] = available_nodes[plane % len(available_nodes)]
        return result

    if policy == "planeGroupPerNode":
        result = {}
        for node_id, facts in pod_inventory.items():
            if facts.get("node_type") == "ground_station":
                result[node_id] = _deterministic_node(node_id, available_nodes)
            else:
                plane = facts.get("plane", 0)
                group = plane // planes_per_group
                result[node_id] = available_nodes[group % len(available_nodes)]
        return result

    raise ValueError(f"Unknown placement policy: {policy}")


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


CHART_NAMESPACE_VALUE = '"{{ .Values.namespace }}"'
_TEMPLATED_FORMS = (None, '"', "'")


def _single_entry(node: yaml.MappingNode, key: str) -> tuple[yaml.ScalarNode, yaml.Node]:
    entries = [
        (key_node, value_node)
        for key_node, value_node in node.value
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key
    ]
    if len(entries) != 1:
        raise ValueError(f"{key!r} must appear exactly once, found {len(entries)}")
    return entries[0]


def render_chart_copy(source_text: str) -> str:
    """The chart's copy of the platform file: the shipped text, validated, with
    the value of ``platform.kubernetes_namespace`` templated to the release
    namespace.

    Validity is the model's, applied to the file as it is. The field is
    located as a YAML node, so its key may be quoted or spaced freely and a
    trailing comment survives. The value must be a plain or quoted scalar on
    one line; a block scalar, anchor, alias or tag is refused before any
    output, since replacing part of such a value would not yield the same
    document with one value changed.
    """
    loader = yaml.SafeLoader(source_text)
    try:
        root = loader.get_single_node()
        data = loader.construct_document(root) if root is not None else None
    finally:
        loader.dispose()
    if (
        not isinstance(root, yaml.MappingNode)
        or not isinstance(data, dict)
        or "platform" not in data
    ):
        raise ValueError("platform configuration must be a mapping with a 'platform' key")
    PlatformConfig.model_validate(data["platform"])
    _, platform_node = _single_entry(root, "platform")
    if not isinstance(platform_node, yaml.MappingNode):
        raise ValueError("'platform' must be a mapping")
    key_node, value_node = _single_entry(platform_node, "kubernetes_namespace")
    unsupported = ValueError(
        "platform.kubernetes_namespace must be a plain or quoted scalar on one line; "
        "block scalars, anchors, aliases and tags are not templated"
    )
    if not isinstance(value_node, yaml.ScalarNode) or value_node.style not in _TEMPLATED_FORMS:
        raise unsupported
    start, end = value_node.start_mark.index, value_node.end_mark.index
    if value_node.start_mark.line != value_node.end_mark.line or start <= key_node.end_mark.index:
        raise unsupported
    lead = source_text[start]
    if (value_node.style is None and lead in "&!*") or (
        value_node.style is not None and lead != value_node.style
    ):
        raise unsupported
    return source_text[:start] + CHART_NAMESPACE_VALUE + source_text[end:]


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m nodalarc.platform_config",
        description="Render the chart's copy of the platform configuration.",
    )
    parser.add_argument(
        "--render-chart-copy",
        metavar="PATH",
        help="validate PATH and write it to stdout with kubernetes_namespace templated",
    )
    args = parser.parse_args(argv)
    if not args.render_chart_copy:
        parser.error("nothing to do: pass --render-chart-copy PATH")
    sys.stdout.write(render_chart_copy(Path(args.render_chart_copy).read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
