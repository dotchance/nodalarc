from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LabelBinding(BaseModel, frozen=True):
    """A single MPLS label forwarding entry."""
    in_label: int                         # incoming MPLS label
    action: str                           # "swap", "pop", or "push"
    out_label: int | None = None          # outgoing label (None for pop)
    out_interface: str                    # outgoing interface name
    backup_out_label: int | None = None   # backup label (for MPLS FRR)
    backup_out_interface: str | None = None  # backup interface

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("swap", "pop", "push"):
            raise ValueError(f"action must be 'swap', 'pop', or 'push', got '{v}'")
        return v


class IngressRule(BaseModel, frozen=True):
    """An LER ingress rule: map a destination prefix to a label push."""
    dst_prefix: str                       # e.g., "172.16.2.0/24"
    push_label: int                       # label to push at ingress
    out_interface: str                    # interface toward first-hop satellite
    backup_push_label: int | None = None
    backup_out_interface: str | None = None


class ForwardingTable(BaseModel, frozen=True):
    """Complete MPLS forwarding state for a single node at a single point in time."""
    node_id: str
    topology_state_id: str                # identifies which topology snapshot this was computed from
    sim_time: str                         # ISO 8601 timestamp this table is valid at
    lsr_bindings: list[LabelBinding]      # transit label switching entries
    ler_ingress_rules: list[IngressRule]  # ingress label push rules (ground stations only)


class AlmanacEntry(BaseModel, frozen=True):
    """A time-indexed collection of forwarding tables for the entire constellation."""
    topology_state_id: str
    sim_time: str
    forwarding_tables: list[ForwardingTable]
    computed_paths: list[str]             # path_ids that were computed for this state
    computation_time_ms: float            # how long the computation took
    is_future: bool = False               # True if computed from lookahead, not yet observed
