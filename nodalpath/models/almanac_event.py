from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlmanacEvent(BaseModel):
    """Event published by NodalPath on the nodalpath-events NATS subject."""

    model_config = ConfigDict(frozen=True)

    event_type: str
    # Values:
    #   "path_computed"       — almanac entry computed for a topology transition
    #   "table_pushed"        — forwarding tables pushed to nodes (result summary)
    #   "deviation_detected"  — TO reported link state differs from almanac expectation
    #   "recomputation_triggered" — deviation caused almanac recomputation

    sim_time: datetime
    wall_time: datetime
    topology_state_id: str

    # Optional fields populated depending on event_type
    node_id: str | None = None  # For node-specific push events
    nodes_attempted: int | None = None  # table_pushed
    nodes_succeeded: int | None = None  # table_pushed
    nodes_failed: int | None = None  # table_pushed
    push_duration_ms: float | None = None  # table_pushed
    deviation_node_a: str | None = None  # deviation_detected
    deviation_node_b: str | None = None  # deviation_detected
    deviation_reason: str | None = None  # deviation_detected (reason from LinkDown)
