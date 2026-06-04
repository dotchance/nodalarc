# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Segment-based session grammar — the new top-level session shape.

``segments`` + ``link_rules`` replace the old single ``constellation`` +
``ground_stations`` fields. This module is the structural envelope only; it
reuses the existing session config models (``SimulationConfig``, ``OrbitConfig``,
``RoutingConfig``, …) verbatim so there is one simulation/orbit/routing contract.

Cross-object and identity-mode semantics (segment/namespace/node-ID uniqueness,
selector cardinality, identity-mode coherence, candidate budgets, ground policy
completeness, runtime-support) are owned by ``resolve_session`` — the single
semantic authority — not duplicated here.
"""

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.models.ephemeris import EphemerisConfig
from nodalarc.models.identity import IdentityConfig
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.segments import Segment
from nodalarc.models.session import (
    AddressingConfig,
    DispatchConfig,
    MiConfig,
    ObservabilityConfig,
    OrbitConfig,
    PlacementConfig,
    PlanePerNodePlacementConfig,
    RoutingConfig,
    SchedulingConfig,
    SessionMeta,
    SimulationConfig,
    TerrestrialLinkConfig,
    TimeConfig,
    TrafficFlowConfig,
)


class SegmentSessionConfig(BaseModel):
    """Top-level segment-based session YAML.

    This is the only product session grammar. ``SessionConfig`` remains an
    internal runtime projection produced by the resolver for mature engines;
    old top-level ``constellation`` / ``ground_stations`` YAML is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    session: SessionMeta
    segments: list[Segment] = Field(min_length=1)
    link_rules: list[LinkRule] = Field(default_factory=list)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    ephemeris: EphemerisConfig | None = None
    orbit: OrbitConfig
    # Session-root scheduling is an explicit defaults/compatibility surface only;
    # effective ground policy is resolved per station by the resolver.
    scheduling: SchedulingConfig | None = None
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    addressing: AddressingConfig = Field(default_factory=AddressingConfig)
    routing: RoutingConfig
    time: TimeConfig = Field(default_factory=TimeConfig)
    traffic_flows: list[TrafficFlowConfig] | None = None
    terrestrial_links: list[TerrestrialLinkConfig] | None = None
    placement: PlacementConfig = Field(default_factory=PlanePerNodePlacementConfig)
    mi: MiConfig = Field(default_factory=MiConfig)
