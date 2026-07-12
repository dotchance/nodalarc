"""Deterministic, behavior-focused projections of resolved session truth.

The projection is a characterization contract, not authored configuration and
not another resolver model. It intentionally includes only runtime semantics
needed to compare resolver behavior while excluding source, run, catalog-path,
and presentation metadata. Collections whose ordering is incidental are sorted;
ordered policies such as ground-station ranking precedence remain ordered.

Run this module in two checkouts to produce directly diffable JSON::

    python -m nodalarc.semantic_projection path/to/session.yaml > projection.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from nodalarc.models.resolved_session import (
    ResolvedBodyFacts,
    ResolvedEndpoint,
    ResolvedEphemeris,
    ResolvedEphemerisKernel,
    ResolvedLinkCandidate,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedOrbitFacts,
    ResolvedRoutingDomain,
    ResolvedSession,
)

SEMANTIC_PROJECTION_SCHEMA = "nodalarc.resolved-semantics.v1"


def _model_json(model: BaseModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _json_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sorted_json(values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(values, key=_json_sort_key)


def _terminal_projection(node: ResolvedNode) -> list[dict[str, Any]]:
    terminals = [
        _without_none(
            {
                "terminal_id": terminal.terminal_id,
                "endpoint_role": terminal.endpoint_role,
                "medium": terminal.medium,
                "link_role": terminal.link_role,
                "count": terminal.count,
                "tracking_capacity": terminal.tracking_capacity,
                "max_range_km": terminal.max_range_km,
                "min_elevation_deg": terminal.min_elevation_deg,
                "field_of_regard_deg": terminal.field_of_regard_deg,
                "tracking_rate_deg_s": terminal.tracking_rate_deg_s,
                "bandwidth_mbps": terminal.bandwidth_mbps,
                "boresight": _model_json(terminal.boresight),
            }
        )
        for terminal in node.terminal_inventory
    ]
    return _sorted_json(terminals)


def _wan_interface_projection(node: ResolvedNode) -> list[dict[str, Any]]:
    interfaces = [
        {
            "name": interface.name,
            "terminal_id": interface.terminal_id,
            "borrows": interface.borrows,
        }
        for interface in node.wan_interfaces
    ]
    return _sorted_json(interfaces)


def _node_projection(node: ResolvedNode) -> dict[str, Any]:
    return _without_none(
        {
            "node_id": node.node_id,
            "local_node_id": node.local_node_id,
            "segment_id": node.segment_id,
            "namespace": node.namespace,
            "placement_groups": sorted(node.placement_groups),
            "kind": node.kind,
            "frame_id": node.frame_id,
            "central_body": node.central_body,
            "reference_body": node.reference_body,
            "forwarding": node.forwarding,
            "service_priority": node.service_priority,
            "plane": node.plane,
            "slot": node.slot,
            "terminal_inventory": _terminal_projection(node),
            "wan_interfaces": _wan_interface_projection(node),
            "ground_scheduling": _model_json(node.ground_scheduling),
            "clock": _model_json(node.clock),
        }
    )


def _prefix_projection(node: ResolvedNode) -> dict[str, list[str]] | None:
    prefixes = node.originated_prefixes
    if prefixes is None:
        return None
    return _without_none(
        {
            "ipv4": sorted(prefixes.ipv4) if prefixes.ipv4 is not None else None,
            "ipv6": sorted(prefixes.ipv6) if prefixes.ipv6 is not None else None,
        }
    )


def _addressing_projection(resolved: ResolvedSession) -> dict[str, Any]:
    nodes = [
        _without_none(
            {
                "node_id": node.node_id,
                "interfaces": _model_json(node.interfaces),
                "originated_prefixes": _prefix_projection(node),
            }
        )
        for node in resolved.nodes
        if node.interfaces is not None or node.originated_prefixes is not None
    ]
    sid_blocks = [
        {
            "domain_id": block.domain_id,
            "node_ids": sorted(block.node_ids),
            "sid_start": block.sid_start,
            "sid_end": block.sid_end,
        }
        for block in resolved.sid_blocks
    ]
    sid_indices = [
        {"node_id": node_id, "sid_index": sid_index}
        for node_id, sid_index in sorted(resolved.sid_index_by_node_id().items())
    ]
    return {
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "sid_blocks": sorted(sid_blocks, key=lambda item: item["domain_id"]),
        "sid_indices": sid_indices,
    }


def _area_assignment_projection(domain: ResolvedRoutingDomain) -> dict[str, Any] | None:
    assignment = _model_json(domain.area_assignment)
    if assignment is None:
        return None
    if "assignments" in assignment:
        normalized = []
        for mapping in assignment["assignments"]:
            mapping = dict(mapping)
            if isinstance(mapping.get("planes"), list):
                mapping["planes"] = sorted(mapping["planes"])
            if isinstance(mapping.get("ground_stations"), list):
                mapping["ground_stations"] = sorted(mapping["ground_stations"])
            normalized.append(mapping)
        assignment["assignments"] = _sorted_json(normalized)
    return assignment


def _routing_domain_projection(domain: ResolvedRoutingDomain) -> dict[str, Any]:
    return _without_none(
        {
            "domain_id": domain.domain_id,
            "protocol": domain.protocol,
            "node_ids": sorted(domain.node_ids),
            "capabilities": sorted(domain.capabilities),
            "area_assignment": _area_assignment_projection(domain),
            "timers": _model_json(domain.timers),
        }
    )


def _routing_boundary_projection(resolved: ResolvedSession) -> list[dict[str, Any]]:
    if resolved.routing is None or resolved.routing.boundaries is None:
        return []
    boundaries: list[dict[str, Any]] = []
    for boundary in resolved.routing.boundaries:
        exports: list[dict[str, Any]] = []
        for export in boundary.export:
            item = export.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(item.get("prefixes"), list):
                item["prefixes"] = sorted(item["prefixes"])
            exports.append(item)
        boundaries.append(
            {
                "over": boundary.over,
                "adapter": boundary.adapter,
                "export": _sorted_json(exports),
            }
        )
    return _sorted_json(boundaries)


def _endpoint_projection(endpoint: ResolvedEndpoint) -> dict[str, Any]:
    return _without_none(
        {
            "segment_id": endpoint.segment_id,
            "terminal_role": endpoint.terminal_role,
            "terminal_medium": endpoint.terminal_medium,
            "terminal_id": endpoint.terminal_id,
            "min_elevation_deg": endpoint.min_elevation_deg,
            "node_ids": sorted(endpoint.node_ids),
        }
    )


def _topology_projection(rule: ResolvedLinkRule) -> dict[str, Any]:
    topology = rule.topology.model_dump(mode="json", by_alias=True, exclude_none=True)
    if "pairs" in topology:
        pairs = [
            {"a": min(pair["a"], pair["b"]), "b": max(pair["a"], pair["b"])}
            for pair in topology["pairs"]
        ]
        topology["pairs"] = _sorted_json(pairs)
    return topology


def _link_rule_projection(rule: ResolvedLinkRule) -> dict[str, Any]:
    return _without_none(
        {
            "rule_id": rule.rule_id,
            "kind": rule.kind,
            "enabled": rule.enabled,
            "endpoints": _sorted_json(
                [_endpoint_projection(endpoint) for endpoint in rule.endpoints]
            ),
            "topology": _topology_projection(rule),
            "constraints": _model_json(rule.constraints),
        }
    )


def _link_candidate_projection(candidate: ResolvedLinkCandidate) -> dict[str, Any]:
    endpoints = _sorted_json(
        [
            _without_none(
                {
                    "node_id": candidate.node_a,
                    "interface": candidate.interface_a,
                    "terminal_role": candidate.terminal_roles[0],
                    "segment_id": candidate.endpoint_segments[0],
                }
            ),
            _without_none(
                {
                    "node_id": candidate.node_b,
                    "interface": candidate.interface_b,
                    "terminal_role": candidate.terminal_roles[1],
                    "segment_id": candidate.endpoint_segments[1],
                }
            ),
        ]
    )
    return _without_none(
        {
            "rule_id": candidate.rule_id,
            "kind": candidate.kind,
            "terminal_medium": candidate.terminal_medium,
            "endpoints": endpoints,
            "bandwidth_mbps": candidate.bandwidth_mbps,
            "topology_mode": candidate.topology_mode,
            "priority": candidate.priority,
        }
    )


def _body_projection(body: ResolvedBodyFacts) -> dict[str, Any]:
    return {
        "body_id": body.body_id,
        "gravitational_parameter_km3_s2": body.gravitational_parameter_km3_s2,
        "mean_radius_km": body.mean_radius_km,
        "equatorial_radius_km": body.equatorial_radius_km,
        "polar_radius_km": body.polar_radius_km,
    }


def _orbit_projection(node_id: str, orbit: ResolvedOrbitFacts) -> dict[str, Any]:
    return _without_none(
        {
            "node_id": node_id,
            "central_body": orbit.central_body,
            "epoch": orbit.epoch,
            "propagator": orbit.propagator,
            "semi_major_axis_km": orbit.semi_major_axis_km,
            "eccentricity": orbit.eccentricity,
            "inclination_deg": orbit.inclination_deg,
            "raan_deg": orbit.raan_deg,
            "argument_of_perigee_deg": orbit.argument_of_perigee_deg,
            "mean_anomaly_deg": orbit.mean_anomaly_deg,
            "tle_line_1": orbit.tle_line_1,
            "tle_line_2": orbit.tle_line_2,
            "norad_id": orbit.norad_id,
        }
    )


def _ephemeris_kernel_projection(kernel: ResolvedEphemerisKernel) -> dict[str, Any]:
    return _without_none(
        {
            "path": kernel.path,
            "sha256": kernel.sha256,
            "targets": sorted(kernel.targets),
            "frame": kernel.frame,
            "coverage_start": kernel.coverage_start,
            "coverage_end": kernel.coverage_end,
        }
    )


def _ephemeris_projection(ephemeris: ResolvedEphemeris | None) -> dict[str, Any] | None:
    if ephemeris is None:
        return None
    return {
        "provider": ephemeris.provider,
        "quality_tier": ephemeris.quality_tier,
        "kernels": _sorted_json(
            [_ephemeris_kernel_projection(kernel) for kernel in ephemeris.kernels]
        ),
    }


def resolved_session_semantic_projection(resolved: ResolvedSession) -> dict[str, Any]:
    """Return the versioned, deterministic behavior projection for a session."""
    if not isinstance(resolved, ResolvedSession):
        raise TypeError("resolved_session_semantic_projection requires a ResolvedSession")

    nodes = sorted((_node_projection(node) for node in resolved.nodes), key=lambda n: n["node_id"])
    domains = sorted(
        (_routing_domain_projection(domain) for domain in resolved.routing_domains),
        key=lambda domain: domain["domain_id"],
    )
    rules = sorted(
        (_link_rule_projection(rule) for rule in resolved.link_rules),
        key=lambda rule: rule["rule_id"],
    )
    candidates = _sorted_json(
        [_link_candidate_projection(candidate) for candidate in resolved.link_candidates]
    )
    bodies = sorted(
        (_body_projection(body) for body in resolved.bodies), key=lambda b: b["body_id"]
    )
    orbits = sorted(
        (
            _orbit_projection(node.node_id, node.orbit)
            for node in resolved.nodes
            if node.orbit is not None
        ),
        key=lambda orbit: orbit["node_id"],
    )
    surface_positions = sorted(
        (
            {
                "node_id": node.node_id,
                **node.surface_position.model_dump(mode="json", by_alias=True, exclude_none=True),
            }
            for node in resolved.nodes
            if node.surface_position is not None
        ),
        key=lambda position: position["node_id"],
    )

    return {
        "schema": SEMANTIC_PROJECTION_SCHEMA,
        "identity_mode": resolved.identity_mode.value,
        "nodes": nodes,
        "addressing": _addressing_projection(resolved),
        "routing": {
            "domains": domains,
            "boundaries": _routing_boundary_projection(resolved),
        },
        "links": {
            "rules": rules,
            "candidates": candidates,
        },
        "physics": {
            "bodies": bodies,
            "orbits": orbits,
            "surface_positions": surface_positions,
        },
        "simulation": _model_json(resolved.simulation),
        "dispatch": _model_json(resolved.dispatch),
        "time": _model_json(resolved.time),
        "ephemeris": _ephemeris_projection(resolved.ephemeris),
    }


def canonical_semantic_projection_json(
    resolved: ResolvedSession, *, indent: int | None = None
) -> str:
    """Serialize a semantic projection deterministically."""
    separators = (",", ":") if indent is None else None
    return json.dumps(
        resolved_session_semantic_projection(resolved),
        sort_keys=True,
        separators=separators,
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    )


def resolved_session_semantic_digest(resolved: ResolvedSession) -> str:
    """Return a SHA-256 identity for the canonical semantic projection."""
    payload = canonical_semantic_projection_json(resolved).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project one resolved NodalArc session into deterministic semantic JSON"
    )
    parser.add_argument("session", type=Path, help="Session YAML to resolve and project")
    parser.add_argument(
        "--catalog-root",
        type=Path,
        default=Path("catalog/nodalarc"),
        help="Root used for nodalarc: references",
    )
    parser.add_argument(
        "--user-catalog-root",
        type=Path,
        default=None,
        help="Optional root used for user: references",
    )
    parser.add_argument(
        "--digest-only",
        action="store_true",
        help="Print only the semantic SHA-256 digest",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print canonical compact JSON instead of indented JSON",
    )
    args = parser.parse_args(argv)

    from nodalarc.catalog_paths import CatalogRoots
    from nodalarc.resolve_session import load_session_resolution_from_file

    roots = CatalogRoots.from_catalog_root(
        args.catalog_root,
        user_root=args.user_catalog_root,
    )
    resolved = load_session_resolution_from_file(
        args.session,
        catalog_roots=roots,
        origin="semantic_projection",
    ).resolved
    if args.digest_only:
        print(resolved_session_semantic_digest(resolved))
    else:
        print(canonical_semantic_projection_json(resolved, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
