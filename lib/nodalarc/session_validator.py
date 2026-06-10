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
    results.extend(_check_selection_score_scale(resolved))
    results.extend(_check_ome_runtime_support(resolved))
    results.extend(_check_access_geometry_feasibility(resolved))
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
    site_lan_neighbors = _site_lan_neighbors_by_node(resolved)
    results: list[ValidationResult] = []
    nodes_by_id = {node.node_id: node for node in resolved.nodes}
    for domain in resolved.routing_domains:
        if len(domain.node_ids) <= 1:
            continue
        domain_members = {
            node_id
            for node_id in domain.node_ids
            if (node := nodes_by_id.get(node_id)) is not None
            and node.forwarding in {"routed", "host"}
        }
        if len(domain_members) <= 1:
            continue
        # Per-node degree is not connectivity: a domain can split into
        # internally-dense islands where every node has neighbors but half
        # the domain is unreachable. Walk the candidate + site-LAN graph and
        # require one connected component.
        components: list[set[str]] = []
        unvisited = set(domain_members)
        while unvisited:
            start = unvisited.pop()
            component = {start}
            frontier = [start]
            while frontier:
                current = frontier.pop()
                neighbors = (
                    candidate_neighbors.get(current, set()) | site_lan_neighbors.get(current, set())
                ) & domain_members
                for neighbor in neighbors:
                    if neighbor in unvisited:
                        unvisited.discard(neighbor)
                        component.add(neighbor)
                        frontier.append(neighbor)
            components.append(component)
        if len(components) == 1:
            continue
        components.sort(key=len, reverse=True)
        detail = "; ".join(
            f"component {index} ({len(component)} nodes, e.g. {', '.join(sorted(component)[:3])})"
            for index, component in enumerate(components, start=1)
        )
        results.append(
            ValidationResult(
                level="error",
                code="E003",
                message=(
                    f"Routing domain {domain.domain_id!r} is not connected: it splits "
                    f"into {len(components)} components — {detail}."
                ),
                remediation=(
                    "Add link rules (or site-LAN membership) joining the components, "
                    "or split the domain along the real connectivity boundary."
                ),
                field_path=f"routing.domains.{domain.domain_id}",
            )
        )
    return results


_ENGINE_DEFAULT_RANKING_ORDER = (
    "service_priority",
    "selection_score",
    "satellite_ground_terminal_capacity",
    "lex_pair",
)

_SELECTION_POLICY_NAMES = {
    "highest_elevation": "highest-elevation",
    "lowest_elevation": "lowest-elevation",
    "longest_remaining_pass": "longest-remaining-pass",
}


def _check_selection_score_scale(resolved: ResolvedSession) -> list[ValidationResult]:
    """E022: 'selection_score' ranking over mixed score scales fails the
    deploy gate — never an OME startup crash."""
    from nodalarc.models.ground_policy import validate_selection_score_scale_compatibility

    policy_names: dict[str, str] = {}
    ranking: tuple[str, ...] | None = None
    for node in resolved.nodes:
        scheduling = node.ground_scheduling
        if node.kind != "ground_station" or scheduling is None:
            continue
        if scheduling.selection_policy is not None:
            for field, name in _SELECTION_POLICY_NAMES.items():
                if getattr(scheduling.selection_policy, field, None) is not None:
                    policy_names[node.node_id] = name
                    break
        if scheduling.ranking_order is not None:
            ranking = tuple(scheduling.ranking_order)
    if not policy_names:
        return []
    try:
        validate_selection_score_scale_compatibility(
            policy_names=policy_names,
            # Allocator-wide ranking is resolve-time uniform; absent means the
            # engine default applies — validate against what will actually run.
            ranking_order=ranking or _ENGINE_DEFAULT_RANKING_ORDER,
        )
    except ValueError as exc:
        return [
            ValidationResult(
                level="error",
                code="E022",
                message=str(exc),
                remediation=(
                    "Use 'per_gs_rank' in ranking_order for cross-policy arbitration, "
                    "or give every ground station a selection policy with the same "
                    "score scale."
                ),
                field_path="segments.apply.scheduling.ranking_order",
            )
        ]
    return []


def _site_lan_neighbors_by_node(resolved: ResolvedSession) -> dict[str, set[str]]:
    """Wired site-LAN adjacencies: routed ground nodes sharing one site's
    terr0 segment are L2 neighbors — real edges created at wiring time, not
    a validation exemption."""
    members_by_site: dict[str, list[str]] = {}
    for node in resolved.nodes:
        if (
            node.kind == "ground_station"
            and node.forwarding == "routed"
            and node.namespace is not None
            and node.interfaces is not None
            and node.interfaces.terr0 is not None
        ):
            members_by_site.setdefault(node.namespace, []).append(node.node_id)
    neighbors: dict[str, set[str]] = {}
    for members in members_by_site.values():
        if len(members) < 2:
            continue
        member_set = set(members)
        for node_id in members:
            neighbors[node_id] = member_set - {node_id}
    return neighbors


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
    non_earth_bodies = sorted(body for body in active_bodies if body != "earth")
    manifest_targets = {
        target
        for kernel in (resolved.ephemeris.kernels if resolved.ephemeris is not None else ())
        for target in kernel.targets
    }
    missing_bodies = sorted(set(non_earth_bodies) - manifest_targets)
    if missing_bodies:
        results.append(
            ValidationResult(
                level="error",
                code="E020",
                message=(
                    "Session contains non-Earth body/bodies "
                    f"{missing_bodies}, but OME did not receive a validated catalog "
                    "ephemeris provider for those bodies."
                ),
                remediation=(
                    "Add a resolved ephemeris manifest that targets every non-Earth "
                    "body before deploying this session."
                ),
                field_path="ephemeris",
            )
        )
    return results


def _check_access_geometry_feasibility(resolved: ResolvedSession) -> list[ValidationResult]:
    """Flag gateways whose access rule can never close geometrically.

    A circular/elliptical orbit's ground track is bounded by its inclination
    band; a site far enough outside that band can never see the constellation
    above its elevation mask, no matter how long the session runs. The bound
    here is deliberately generous (apoapsis altitude at the band's closest
    approach to the site latitude), so a W005 means truly impossible, not
    merely rare — the trap is content that LOOKS wired (candidates exist,
    pods deploy) but can never schedule a link.
    """
    import math

    radius_by_body = {facts.body_id: facts.mean_radius_km for facts in resolved.bodies}
    node_by_id = {node.node_id: node for node in resolved.nodes}
    min_elev_by_gs = resolved.effective_ground_min_elevation_by_gs()

    # (gs, rule) -> best achievable elevation across that rule's candidates
    best_by_pair: dict[tuple[str, str], float] = {}
    for candidate in resolved.link_candidates:
        if candidate.kind != "access":
            continue
        node_a = node_by_id[candidate.node_a]
        node_b = node_by_id[candidate.node_b]
        ground, sat = (node_a, node_b) if node_a.kind == "ground_station" else (node_b, node_a)
        if ground.surface_position is None or sat.orbit is None:
            continue
        body_radius = radius_by_body.get(sat.orbit.central_body)
        if body_radius is None:
            continue
        inclination = sat.orbit.inclination_deg % 180.0
        band_deg = min(inclination, 180.0 - inclination)
        offset_deg = max(0.0, abs(ground.surface_position.lat_deg) - band_deg)
        r_apo = sat.orbit.semi_major_axis_km * (1.0 + sat.orbit.eccentricity)
        if offset_deg <= 0.0:
            best_elevation = 90.0
        else:
            offset = math.radians(offset_deg)
            best_elevation = math.degrees(
                math.atan2(math.cos(offset) - body_radius / r_apo, math.sin(offset))
            )
        key = (ground.node_id, candidate.rule_id)
        best_by_pair[key] = max(best_by_pair.get(key, -90.0), best_elevation)

    results: list[ValidationResult] = []
    for (gs_id, rule_id), best_elevation in sorted(best_by_pair.items()):
        mask = min_elev_by_gs.get(gs_id, 0.0)
        if best_elevation < mask:
            results.append(
                ValidationResult(
                    level="warning",
                    code="W005",
                    message=(
                        f"Ground station {gs_id!r} can never see any satellite of "
                        f"access rule {rule_id!r} above its {mask:.0f} degree "
                        f"elevation mask (best achievable elevation "
                        f"{best_elevation:.1f} degrees - the site lies outside the "
                        "constellation's inclination band)"
                    ),
                    remediation=(
                        "Move the site inside the constellation's ground-track "
                        "band, lower the elevation mask, or pair the site with a "
                        "constellation that covers its latitude"
                    ),
                    field_path=f"link_rules.{rule_id}",
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
