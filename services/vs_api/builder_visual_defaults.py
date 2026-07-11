"""Backend-owned visual authoring seeds exposed only through typed operations."""

from __future__ import annotations

from copy import deepcopy

from nodalarc.catalog_refs import BodyRef
from nodalarc.model_validation import TerminalMedium
from nodalarc.models.builder_api import JsonDocument
from nodalarc.models.builder_visual_api import (
    BuilderVisualOrbitPropagator,
    BuilderVisualOrbitShape,
    BuilderVisualPhasingMode,
    BuilderVisualSchedulingPreset,
    BuilderVisualTopologyMode,
)
from nodalarc.models.catalog import ForwardingClass, MountRole
from nodalarc.models.segment_session import RoutingBoundaryAdapter, RoutingProtocol

DEFAULT_PHASING_MODE: BuilderVisualPhasingMode = "walker_delta"
SINGLE_PLANE_PHASING_MODE: BuilderVisualPhasingMode = "evenly_spaced_mean_anomaly"
DEFAULT_SCHEDULING_PRESET: BuilderVisualSchedulingPreset = "leo-fast-handover"
DEFAULT_MOUNT_ROLE: MountRole = "access"
DEFAULT_TERMINAL_MOUNT_COUNT = 1
DEFAULT_BODY_REF = BodyRef("nodalarc:bodies/earth.yaml")
DEFAULT_COMPONENT_IDS: dict[str, str] = {
    "bodies": "my-body",
    "terminals": "my-terminal",
    "payloads": "my-payload",
    "orbits": "my-orbit",
    "nodes": "my-node",
    "sites": "my-site",
    "site-sets": "my-site-set",
    "constellations": "my-constellation",
    "space-node-sets": "my-space-node-set",
}

MOUNT_ROLE_LABELS: tuple[tuple[MountRole, str, str], ...] = (
    ("access", "access", "space ↔ ground"),
    ("isl", "isl", "fabric within a constellation"),
    ("crosslink", "crosslink", "link between constellations"),
    ("backbone", "backbone", "trunk between relay tiers"),
)
LINK_MEDIUM_LABELS: tuple[tuple[TerminalMedium, str, JsonDocument], ...] = (
    ("rf", "RF", {"band": "", "frequency_hz": 0}),
    ("optical", "optical", {"wavelength_nm": 0}),
)
FORWARDING_CLASS_LABELS: tuple[tuple[ForwardingClass, str], ...] = (
    ("routed", "routed"),
    ("host", "host"),
    ("bridge", "bridge"),
    ("control_only", "control only"),
)
ROUTING_PROTOCOL_LABELS: tuple[tuple[RoutingProtocol, str], ...] = (
    ("isis", "IS-IS"),
    ("ospf", "OSPF"),
    ("bgp", "BGP"),
    ("static", "static"),
)
BOUNDARY_ADAPTER_LABELS: tuple[tuple[RoutingBoundaryAdapter, str], ...] = (
    ("static_ip", "static IP"),
    ("bgp", "BGP"),
    ("dtn_bundle", "DTN bundle"),
)
PHASING_MODE_LABELS: tuple[tuple[BuilderVisualPhasingMode, str], ...] = (
    ("walker_delta", "Walker delta"),
    ("walker_star", "Walker star"),
    ("evenly_spaced_mean_anomaly", "single-plane evenly spaced"),
)
ORBIT_SHAPE_LABELS: tuple[tuple[BuilderVisualOrbitShape, str], ...] = (
    ("circular", "circular"),
    ("elliptical", "elliptical"),
)
ORBIT_PROPAGATOR_LABELS: tuple[tuple[BuilderVisualOrbitPropagator, str], ...] = (
    ("two_body", "two body"),
    ("j2_mean_elements", "J2 mean elements"),
)
TOPOLOGY_MODE_LABELS: tuple[tuple[BuilderVisualTopologyMode, str], ...] = (
    ("visible_candidates", "all visible pairs"),
    ("nearest_n", "nearest N"),
)

SCHEDULING_PRESET_LABELS: tuple[tuple[BuilderVisualSchedulingPreset, str], ...] = (
    ("leo-fast-handover", "LEO fast handover — make-before-break"),
    ("geo-longest-pass", "GEO longest pass — break-before-make"),
)

_ALLOCATOR_SCHEDULING: JsonDocument = {
    "ranking_order": [
        "service_priority",
        "per_gs_rank",
        "satellite_ground_terminal_capacity",
        "lex_pair",
    ],
    "mbb_preemption": "off",
    "successor_abort_policy": "hard_release",
    "cross_tenant_displacement": "off",
    "bbm_acquire_timeout_ticks": 1,
}
_SCHEDULING_PRESET_BLOCKS: dict[BuilderVisualSchedulingPreset, JsonDocument] = {
    "leo-fast-handover": {
        "selection_policy": {"highest_elevation": {}},
        "handover_policy": {"hysteresis": {"discount_factor": 1.1, "mask_fade_range_deg": 3.0}},
        "handover_mode": "mbb",
        "mbb_overlap_ticks": 30,
        "mbb_reserve": 1,
        "handover_concurrency": "one_at_a_time",
        **_ALLOCATOR_SCHEDULING,
    },
    "geo-longest-pass": {
        "selection_policy": {"longest_remaining_pass": {"lookahead_horizon_ticks": 600}},
        "handover_policy": {"hard_release": {}},
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 0,
        "mbb_reserve": 0,
        "handover_concurrency": "one_at_a_time",
        **_ALLOCATOR_SCHEDULING,
    },
}


def scheduling_preset_block(preset: BuilderVisualSchedulingPreset) -> JsonDocument:
    """Return an isolated complete scheduling block for one typed preset."""

    return deepcopy(_SCHEDULING_PRESET_BLOCKS[preset])
