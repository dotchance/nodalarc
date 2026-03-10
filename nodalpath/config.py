from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class NodalPathConfig:
    """Runtime configuration for NodalPath.

    Populated from CLI args and/or session YAML nodalpath section.
    """

    session_path: Path

    # Mode
    mode: str = "live"  # "live" | "batch"

    # Live mode ZMQ
    ome_connect: str = ""  # Filled from zmq_channels if empty
    to_connect: str = ""  # Filled from zmq_channels if empty
    events_bind: str = ""  # Filled from zmq_channels if empty

    # Batch mode
    timeline_path: Path | None = None
    output_path: Path | None = None  # JSONL almanac output

    # Push
    transport: str = "grpc"  # "grpc" | "vtysh"
    grpc_port: int = 50051
    namespace: str = "nodalarc"
    push_timeout_seconds: int = 10
    use_incremental_diff: bool = True
    dry_run: bool = False

    # Almanac
    lead_time_seconds: int = 3  # Push N seconds of sim_time before transition

    # Lookahead
    lookahead_enabled: bool = True
    lookahead_horizon_s: int = 5700  # ~1 LEO orbital period

    # Almanac persistence
    almanac_output_path: Path | None = None

    def __post_init__(self) -> None:
        from nodalarc.zmq_channels import (
            NODALPATH_EVENTS_BIND,
            OME_EVENTS_CONNECT,
            TO_EVENTS_CONNECT,
        )

        if not self.ome_connect:
            self.ome_connect = OME_EVENTS_CONNECT
        if not self.to_connect:
            self.to_connect = TO_EVENTS_CONNECT
        if not self.events_bind:
            self.events_bind = NODALPATH_EVENTS_BIND
