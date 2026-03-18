"""NodalPath platform configuration — single source of truth for controller settings.

Loads from configs/nodalpath.yaml. No fallback defaults in Python code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class NodalPathPlatformConfig(BaseModel):
    """Frozen Pydantic model for NodalPath-specific configuration.

    All fields are required — no defaults. The YAML file is the single
    source of truth.
    """

    model_config = ConfigDict(frozen=True)

    # Cross-reference to platform config
    platform_config_path: str

    # Segment Routing MPLS label allocation
    satellite_sid_range_start: int
    ground_station_sid_range_start: int
    adjacency_sid_range_start: int

    # gRPC forwarding table push
    grpc_push_timeout_seconds: int
    grpc_push_max_parallel_workers: int

    # Lookahead pre-computation
    lookahead_horizon_sim_seconds: int
    lookahead_poll_interval_seconds: float

    # Push scheduling
    push_lead_time_sim_seconds: int

    # Node inspection and feedback loop
    inspection_max_retained_runs: int
    inspection_heartbeat_interval_seconds: int

    # Operator console in-memory buffers
    console_push_history_max_entries: int
    console_deviation_history_max_entries: int
    console_almanac_history_max_entries: int
    console_event_log_max_entries: int


# --- Module-level singleton ---

_config: NodalPathPlatformConfig | None = None


def init_nodalpath_config(source: Path | NodalPathPlatformConfig) -> NodalPathPlatformConfig:
    """Initialize the NodalPath config singleton.

    Args:
        source: Path to nodalpath.yaml or a pre-built config (for tests).

    Returns:
        The initialized NodalPathPlatformConfig.
    """
    global _config
    if isinstance(source, NodalPathPlatformConfig):
        _config = source
    else:
        raw = yaml.safe_load(source.read_text())
        _config = NodalPathPlatformConfig.model_validate(raw["nodalpath"])
    return _config


def get_nodalpath_config() -> NodalPathPlatformConfig:
    """Return the NodalPath config singleton.

    Raises RuntimeError if init_nodalpath_config() has not been called.
    """
    if _config is None:
        raise RuntimeError(
            "NodalPathPlatformConfig not initialized. Call init_nodalpath_config() first."
        )
    return _config


def reset_nodalpath_config() -> None:
    """Reset the singleton (for tests only)."""
    global _config
    _config = None
