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

    # Live mode NATS (connection handled by nats_channels.nats_url())
    nats_url: str = ""  # Filled from nats_channels if empty

    # Batch mode
    timeline_path: Path | None = None
    output_path: Path | None = None  # JSONL almanac output

    # Push
    transport: str = "grpc"  # "grpc" | "vtysh"
    grpc_port: int = 50052
    namespace: str = "nodalarc"
    push_timeout_seconds: int = 10
    use_incremental_diff: bool = True
    dry_run: bool = False

    # Almanac
    lead_time_seconds: int = 3  # Push N seconds of sim_time before transition

    # Lookahead
    lookahead_enabled: bool = True
    lookahead_horizon_s: int = 5700  # ~1 LEO orbital period

    # Inspection / feedback loop
    inspection_heartbeat_interval_s: int = 0  # 0 = disabled
    inspection_on_push: bool = True
    inspection_on_link_event: bool = True

    # Almanac persistence
    almanac_output_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.nats_url:
            from nodalarc.nats_channels import nats_url

            self.nats_url = nats_url()
