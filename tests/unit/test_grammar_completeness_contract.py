# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Grammar completeness contract.

Every field the session grammar models accept must be classified: either it
reaches a consumer (drives resolution, lands on the resolved truth, or renders)
or the resolver rejects it with a typed reason. A field that validates and then
silently does nothing is a trust violation — the session appears configured in
a way it is not running.

This test enumerates every leaf field path of ``SegmentSessionConfig`` and
requires an explicit disposition for each. Adding a grammar field without
classifying it here fails the build; classifying it forces the audit.
"""

from __future__ import annotations

import types
from typing import Union, get_args, get_origin

from nodalarc.models.segment_session import SegmentSessionConfig
from pydantic import BaseModel

CONSUMED = "consumed"  # drives resolution / resolved truth / rendering
REJECTED = "rejected_typed"  # resolver fails loudly when declared
DESCRIPTIVE = "descriptive"  # human-facing text with no runtime meaning by definition

# Longest-prefix match. Every leaf path must match exactly one rule; every rule
# must match at least one leaf path (stale rules fail too).
GRAMMAR_FIELD_DISPOSITION: dict[str, str] = {
    # --- session metadata ---
    "session.name": CONSUMED,
    "session.display_name": DESCRIPTIVE,
    "session.description": DESCRIPTIVE,
    # --- segments ---
    "segments.id": CONSUMED,
    "segments.display_name": DESCRIPTIVE,
    "segments.tags": CONSUMED,
    "segments.source": CONSUMED,
    "segments.placement.from_site_set": CONSUMED,
    "segments.clock.model": CONSUMED,  # session model threads; affine rejects typed
    "segments.clock.offset_s": CONSUMED,
    "segments.clock.rate": CONSUMED,
    "segments.apply.tags": CONSUMED,
    "segments.apply.scheduling": CONSUMED,
    "segments.apply.originated_prefixes": CONSUMED,
    "segments.overrides.match.site": CONSUMED,
    "segments.overrides.tags": CONSUMED,
    "segments.overrides.scheduling": CONSUMED,
    "segments.overrides.originated_prefixes": CONSUMED,
    "segments.frame.lagrange": REJECTED,  # lagrange placement is runtime-future
    "segments.node": REJECTED,  # lagrange segment member
    # --- link rules ---
    "link_rules.id": CONSUMED,
    "link_rules.class": CONSUMED,
    "link_rules.enabled": CONSUMED,
    "link_rules.tags": CONSUMED,
    "link_rules.endpoints.select": CONSUMED,
    "link_rules.endpoints.terminal": CONSUMED,
    "link_rules.endpoints.min_elevation_deg": CONSUMED,  # effective GS mask
    "link_rules.topology.mode": CONSUMED,
    "link_rules.topology.n": CONSUMED,
    "link_rules.topology.pairs": CONSUMED,
    "link_rules.constraints.max_links_per_node": CONSUMED,  # static degree bound
    "link_rules.constraints.max_range_km": REJECTED,  # dynamic; OME doesn't consume yet
    "link_rules.constraints.require_mutual_visibility": REJECTED,
    # --- addressing ---
    "addressing.loopbacks.id": CONSUMED,
    "addressing.loopbacks.applies_to": CONSUMED,
    "addressing.loopbacks.ipv4_pool": CONSUMED,
    "addressing.loopbacks.ipv6_pool": CONSUMED,
    "addressing.loopbacks.prefix_length": CONSUMED,
    "addressing.loopbacks.allocation": CONSUMED,  # by_node_order; others reject typed
    "addressing.point_to_point": REJECTED,  # WAN ifaces are unnumbered (borrow lo0)
    "addressing.terrestrial_prefixes": REJECTED,  # sites author terr0 directly
    # --- routing ---
    "routing.domains.id": CONSUMED,
    "routing.domains.protocol": CONSUMED,
    "routing.domains.selectors": CONSUMED,
    "routing.domains.capabilities.segment_routing.data_plane": CONSUMED,
    "routing.domains.capabilities.traffic_engineering.data_planes": CONSUMED,
    "routing.domains.area_assignment": CONSUMED,
    "routing.domains.timers": CONSUMED,
    "routing.boundaries.over": CONSUMED,
    "routing.boundaries.adapter": CONSUMED,
    "routing.boundaries.export.from": CONSUMED,
    "routing.boundaries.export.to": CONSUMED,
    "routing.boundaries.export.prefixes.aggregate_of": CONSUMED,
    "routing.boundaries.export.export_node_loopbacks": CONSUMED,
    "routing.boundaries.export.install_via": CONSUMED,
    # --- ephemeris / simulation / time / dispatch ---
    "ephemeris.provider": CONSUMED,
    "ephemeris.quality_tier": CONSUMED,
    "ephemeris.kernels.id": CONSUMED,
    "ephemeris.kernels.path": CONSUMED,
    "ephemeris.kernels.sha256": CONSUMED,
    "ephemeris.kernels.targets": CONSUMED,
    "ephemeris.kernels.frame": CONSUMED,
    "ephemeris.kernels.coverage_start": CONSUMED,
    "ephemeris.kernels.coverage_end": CONSUMED,
    "simulation.candidate_limits.max_pairs_per_rule": CONSUMED,
    "simulation.candidate_limits.max_pairs_per_tick": CONSUMED,
    "time.start_time": CONSUMED,
    "time.step_seconds": CONSUMED,
    "time.compression": CONSUMED,
    "dispatch.latency_authority": CONSUMED,
    "dispatch.max_latency_age_ticks": CONSUMED,
    # orbit.default_propagator can never apply (orbit primitives must declare
    # their propagator) — declared values reject with an explanation.
    "orbit.default_propagator": REJECTED,
}


def _models_in(annotation) -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []

    def walk(ann) -> None:
        origin = get_origin(ann)
        if origin in (Union, types.UnionType) or origin in (tuple, list, dict, frozenset, set):
            for arg in get_args(ann):
                walk(arg)
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            found.append(ann)

    walk(annotation)
    return found


def _leaf_paths() -> set[str]:
    paths: set[str] = set()

    def walk_model(model: type[BaseModel], prefix: str, visited: frozenset) -> None:
        if model in visited:
            return
        for name, field in model.model_fields.items():
            label = field.alias or name
            path = f"{prefix}.{label}" if prefix else label
            submodels = _models_in(field.annotation)
            if submodels:
                for sub in submodels:
                    walk_model(sub, path, visited | {model})
            else:
                paths.add(path)

    walk_model(SegmentSessionConfig, "", frozenset())
    return paths


def _disposition_for(path: str) -> tuple[str, str] | None:
    candidates = [
        rule for rule in GRAMMAR_FIELD_DISPOSITION if path == rule or path.startswith(rule + ".")
    ]
    if not candidates:
        return None
    rule = max(candidates, key=len)
    return rule, GRAMMAR_FIELD_DISPOSITION[rule]


def test_every_grammar_field_is_consumed_or_rejected_typed() -> None:
    paths = _leaf_paths()
    assert paths, "grammar enumeration produced no fields"

    unclassified = sorted(path for path in paths if _disposition_for(path) is None)
    assert not unclassified, (
        "session grammar fields with no audited disposition (classify each in "
        "GRAMMAR_FIELD_DISPOSITION as consumed, rejected_typed, or descriptive — "
        "a field that validates and silently does nothing is forbidden): "
        f"{unclassified}"
    )

    matched_rules = {
        _disposition_for(path)[0] for path in paths if _disposition_for(path) is not None
    }
    stale = sorted(set(GRAMMAR_FIELD_DISPOSITION) - matched_rules)
    assert not stale, f"disposition rules matching no grammar field (remove them): {stale}"


def test_disposition_vocabulary_is_closed() -> None:
    assert set(GRAMMAR_FIELD_DISPOSITION.values()) <= {CONSUMED, REJECTED, DESCRIPTIVE}
