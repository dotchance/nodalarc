# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Pre-deployment validation for resolved catalog sessions.

The resolver is the authority that turns catalog YAML into runtime truth. This
module does not load files, re-expand constellations, or reconstruct old session
views. It only inspects a ``ResolvedSession`` and returns user-facing validation
results for conditions that are valid model states but unsuitable for deployment
with the current runtime.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from nodalarc.models.events import ValidationReport, ValidationResult
from nodalarc.models.ground_policy import VALID_SELECTION_POLICY_NAMES
from nodalarc.models.resolved_session import ResolvedSession

# Kept as the public constant for callers that surface scheduling-policy help.
VALID_SCHEDULING_POLICIES = VALID_SELECTION_POLICY_NAMES


def validate_session_readiness(
    resolved: ResolvedSession,
    *,
    available_node_count: int = 1,
) -> list[ValidationResult]:
    """Validate a resolved catalog session before deployment.

    ``resolved`` must already be the frozen runtime object produced by
    ``resolve_session()``. Passing retired session/ground/constellation
    projections is intentionally unsupported.
    """
    if not isinstance(resolved, ResolvedSession):
        raise TypeError("validate_session_readiness requires a ResolvedSession")

    results: list[ValidationResult] = []
    results.extend(_check_link_rules_have_candidates(resolved))
    results.extend(_check_routing_domain_connectivity(resolved))
    results.extend(_check_segment_routing_indices(resolved))
    results.extend(_check_ground_mbb_capacity(resolved))
    results.extend(_check_ome_runtime_support(resolved))
    results.extend(_check_available_node_count(resolved, available_node_count))
    return results


def build_validation_report(
    resolved: ResolvedSession,
    results: list[ValidationResult],
) -> ValidationReport:
    """Build the user-facing validation report from readiness results."""
    if not isinstance(resolved, ResolvedSession):
        raise TypeError("build_validation_report requires a ResolvedSession")

    errors = tuple(result for result in results if result.level == "error")
    warnings = tuple(result for result in results if result.level == "warning")
    return ValidationReport(
        status="invalid" if errors else "valid",
        normalized_schema_version=1,
        effective_config=resolved.model_dump(mode="json"),
        errors=errors,
        warnings=warnings,
        dispatchable=not errors,
    )


def _check_link_rules_have_candidates(resolved: ResolvedSession) -> list[ValidationResult]:
    """Every enabled link rule must resolve to at least one candidate pair."""
    candidates_by_rule = {candidate.rule_id for candidate in resolved.link_candidates}
    results: list[ValidationResult] = []
    for rule in resolved.link_rules:
        if not rule.enabled:
            continue
        if rule.rule_id in candidates_by_rule:
            continue
        results.append(
            ValidationResult(
                level="error",
                code="E003",
                message=(
                    f"Link rule {rule.rule_id!r} resolved to zero candidate links. "
                    "No runtime links can form for that declared relationship."
                ),
                remediation=(
                    "Check the rule selectors, terminal role/medium selectors, "
                    "topology constraints, and candidate limits."
                ),
                field_path=f"link_rules.{rule.rule_id}",
            )
        )
    return results


def _check_routing_domain_connectivity(resolved: ResolvedSession) -> list[ValidationResult]:
    """IGP/static domains should not contain routed members with no candidate edge.

    This is intentionally per-domain. The retired validator used one global
    routing protocol; catalog sessions can run multiple domains.
    """
    candidate_neighbors = _candidate_neighbors_by_node(resolved.link_candidates)
    results: list[ValidationResult] = []
    nodes_by_id = {node.node_id: node for node in resolved.nodes}
    for domain in resolved.routing_domains:
        if len(domain.node_ids) <= 1:
            continue
        domain_members = set(domain.node_ids)
        for node_id in domain.node_ids:
            node = nodes_by_id.get(node_id)
            if node is None or node.forwarding not in {"routed", "host"}:
                continue
            domain_neighbors = candidate_neighbors.get(node_id, set()) & domain_members
            if domain_neighbors:
                continue
            results.append(
                ValidationResult(
                    level="error",
                    code="E003",
                    message=(
                        f"Routing domain {domain.domain_id!r} includes node {node_id!r}, "
                        "but that node has no resolved link candidate to another member "
                        "of the same domain."
                    ),
                    remediation=(
                        "Adjust the routing-domain selector or link rules so every "
                        "routed domain member has at least one declared adjacency."
                    ),
                    field_path=f"routing.domains.{domain.domain_id}",
                )
            )
    return results


def _check_segment_routing_indices(resolved: ResolvedSession) -> list[ValidationResult]:
    """Report SID allocation problems from the resolved session helper."""
    try:
        resolved.sid_index_by_node_id()
    except ValueError as exc:
        return [
            ValidationResult(
                level="error",
                code="E004",
                message=f"Resolved prefix-SID indices are invalid: {exc}",
                remediation="Fix routing-domain segment-routing capability or SID allocation.",
                field_path="sid_blocks",
            )
        ]
    return []


def _check_ground_mbb_capacity(resolved: ResolvedSession) -> list[ValidationResult]:
    """MBB ground policy requires enough access capacity on that ground node."""
    results: list[ValidationResult] = []
    for node in resolved.nodes:
        if node.kind != "ground_station" or node.ground_scheduling is None:
            continue
        scheduling = node.ground_scheduling
        if scheduling.handover_mode != "mbb":
            continue
        reserve = scheduling.mbb_reserve or 0
        required = 1 + reserve
        access_capacity = _terminal_capacity(
            block.count * (block.tracking_capacity or 0)
            for block in node.terminal_inventory
            if block.endpoint_role == "access"
        )
        if access_capacity >= required:
            continue
        results.append(
            ValidationResult(
                level="error",
                code="E021",
                message=(
                    f"Ground node {node.node_id!r} requests MBB with reserve {reserve}, "
                    f"but has access capacity {access_capacity}; required {required}."
                ),
                remediation=(
                    "Increase installed access-terminal capacity for this placed node "
                    "or configure BBM for this ground node."
                ),
                field_path=f"segments.{node.segment_id}.nodes.{node.local_node_id}.scheduling",
            )
        )
    return results


def _check_ome_runtime_support(resolved: ResolvedSession) -> list[ValidationResult]:
    """Known current OME runtime limits must be visible before deployment."""
    results: list[ValidationResult] = []
    for node in resolved.nodes:
        if node.kind != "satellite" or node.orbit is None:
            continue
        if node.orbit.eccentricity != 0.0:
            results.append(
                ValidationResult(
                    level="error",
                    code="E020",
                    message=(
                        f"Satellite {node.node_id!r} uses eccentric orbit "
                        f"{node.orbit.orbit_id!r}; current OME propagation accepts "
                        "only circular resolved orbit inputs."
                    ),
                    remediation=(
                        "Implement the eccentric propagation runtime input before "
                        "deploying this session."
                    ),
                    field_path=f"segments.{node.segment_id}.orbit",
                )
            )
        if node.orbit.propagator == "sgp4_tle":
            results.append(
                ValidationResult(
                    level="error",
                    code="E020",
                    message=(
                        f"Satellite {node.node_id!r} uses sgp4_tle, but TLE records "
                        "are not yet materialized into the OME input bundle."
                    ),
                    remediation="Materialize validated TLE records into ResolvedSession/OME inputs.",
                    field_path=f"segments.{node.segment_id}.orbit",
                )
            )

    active_bodies = {
        body
        for node in resolved.nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    }
    unsupported_bodies = sorted(body for body in active_bodies if body != "earth")
    if unsupported_bodies:
        results.append(
            ValidationResult(
                level="error",
                code="E020",
                message=(
                    "Session contains non-Earth body/bodies "
                    f"{unsupported_bodies}, but OME does not yet receive a validated "
                    "catalog ephemeris provider for non-Earth bodies."
                ),
                remediation=(
                    "Materialize the catalog ephemeris manifest into ResolvedSession "
                    "and OME before deploying non-Earth sessions."
                ),
                field_path="ephemeris",
            )
        )
    return results


def _check_available_node_count(
    resolved: ResolvedSession,
    available_node_count: int,
) -> list[ValidationResult]:
    """Warn when routed pods outnumber available Kubernetes nodes."""
    routed_nodes = [node for node in resolved.nodes if node.forwarding == "routed"]
    if available_node_count <= 0 or len(routed_nodes) <= available_node_count:
        return []
    return [
        ValidationResult(
            level="warning",
            code="W004",
            message=(
                f"Session has {len(routed_nodes)} routed nodes but only "
                f"{available_node_count} Kubernetes node(s) available."
            ),
            remediation=(
                "Use a placement policy appropriate for the cluster size or add "
                "Kubernetes worker nodes."
            ),
            field_path="segments",
        )
    ]


def _candidate_neighbors_by_node(candidates: Iterable) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        neighbors[candidate.node_a].add(candidate.node_b)
        neighbors[candidate.node_b].add(candidate.node_a)
    return neighbors


def _terminal_capacity(values: Iterable[int]) -> int:
    return sum(values)
