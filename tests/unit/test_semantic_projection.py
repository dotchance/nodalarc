"""Characterization tests for deterministic resolved-session projections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.segment_session import SessionMeta
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.semantic_projection import (
    SEMANTIC_PROJECTION_SCHEMA,
    canonical_semantic_projection_json,
    main,
    resolved_session_semantic_digest,
    resolved_session_semantic_projection,
)

from tests.catalog_session_fixtures import (
    ISS_TLE_LINE_1,
    build_catalog_session_fixture,
    install_tle_space_node_set,
    resolve_catalog_session,
)

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-heo-geo-luna-reachability.yaml"


@pytest.fixture(scope="module")
def resolved():
    return load_session_resolution_from_file(
        SESSION,
        origin="test.semantic_projection",
        run_id="run-characterization-0001",
    ).resolved


def test_projection_covers_approved_resolved_semantics(resolved) -> None:
    projection = resolved_session_semantic_projection(resolved)

    assert projection["schema"] == SEMANTIC_PROJECTION_SCHEMA
    assert set(projection) == {
        "schema",
        "identity_mode",
        "nodes",
        "addressing",
        "routing",
        "links",
        "physics",
        "simulation",
        "dispatch",
        "time",
        "ephemeris",
    }
    assert len(projection["nodes"]) == len(resolved.nodes)
    assert len(projection["links"]["candidates"]) == len(resolved.link_candidates)
    assert {domain["domain_id"] for domain in projection["routing"]["domains"]} == {
        domain.domain_id for domain in resolved.routing_domains
    }
    assert projection["routing"]["boundaries"]
    assert projection["addressing"]["sid_indices"]
    assert {body["body_id"] for body in projection["physics"]["bodies"]} == {
        "earth",
        "luna",
    }
    assert projection["physics"]["orbits"]
    assert projection["physics"]["surface_positions"]
    assert projection["time"] == resolved.time.model_dump(mode="json")
    assert projection["ephemeris"]["kernels"]
    access_candidate = next(
        candidate
        for candidate in projection["links"]["candidates"]
        if candidate["kind"] == "access"
    )
    assert all("interface" not in endpoint for endpoint in access_candidate["endpoints"])


def test_projection_commits_to_exact_canonical_tle_records() -> None:
    raw = build_catalog_session_fixture(
        name="semantic-sgp4",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    source_ref = install_tle_space_node_set(raw)
    baseline = resolve_catalog_session(raw)
    baseline_projection = resolved_session_semantic_projection(baseline)
    iss = next(
        orbit for orbit in baseline_projection["physics"]["orbits"] if orbit["norad_id"] == 25544
    )

    assert iss["tle_line_1"] == ISS_TLE_LINE_1

    source = raw.read_catalog(source_ref)
    source["space_node_set"]["nodes"][0]["sgp4_tle"]["line_1"] = ISS_TLE_LINE_1.replace(
        "21075.51041667",
        "21076.51041667",
    )
    raw.write_catalog(source_ref, source)
    changed = resolve_catalog_session(raw)

    assert resolved_session_semantic_digest(changed) != resolved_session_semantic_digest(baseline)


def test_projection_excludes_source_run_catalog_and_presentation_metadata(resolved) -> None:
    changed_nodes = []
    for node in resolved.nodes:
        terminals = tuple(
            terminal.model_copy(
                update={
                    "source_ref": f"user:replacement/{terminal.terminal_id}.yaml",
                    "source_terminal_id": "renamed-source-terminal",
                }
            )
            for terminal in node.terminal_inventory
        )
        orbit = (
            node.orbit.model_copy(update={"orbit_id": "renamed-source-orbit"})
            if node.orbit is not None
            else None
        )
        changed_nodes.append(
            node.model_copy(
                update={
                    "tags": tuple(reversed(node.tags)),
                    "satellite_type": "renamed-source-node",
                    "terminal_inventory": terminals,
                    "orbit": orbit,
                }
            )
        )

    changed_bodies = tuple(
        body.model_copy(
            update={"display_name": "Presentation only", "reference": "urn:nodalarc:other"}
        )
        for body in resolved.bodies
    )
    changed_ephemeris = resolved.ephemeris.model_copy(
        update={
            "kernels": tuple(
                kernel.model_copy(update={"id": f"renamed-{index}"})
                for index, kernel in enumerate(resolved.ephemeris.kernels)
            )
        }
    )
    changed = resolved.model_copy(
        update={
            "session": SessionMeta(
                name="renamed-session",
                display_name="Presentation only",
                description="Presentation only",
            ),
            "nodes": tuple(changed_nodes),
            "bodies": changed_bodies,
            "ephemeris": changed_ephemeris,
            "source_context": SourceContext(
                origin="other.consumer",
                session_path="/different/source/session.yaml",
                run_id="run-characterization-9999",
            ),
        }
    )

    assert resolved_session_semantic_projection(changed) == resolved_session_semantic_projection(
        resolved
    )
    assert resolved_session_semantic_digest(changed) == resolved_session_semantic_digest(resolved)


def test_projection_normalizes_incidental_collection_and_endpoint_order(resolved) -> None:
    nodes = tuple(
        node.model_copy(
            update={
                "placement_groups": tuple(reversed(node.placement_groups)),
                "terminal_inventory": tuple(reversed(node.terminal_inventory)),
                "wan_interfaces": tuple(reversed(node.wan_interfaces)),
                "originated_prefixes": (
                    node.originated_prefixes.model_copy(
                        update={
                            "ipv4": (
                                tuple(reversed(node.originated_prefixes.ipv4))
                                if node.originated_prefixes.ipv4 is not None
                                else None
                            ),
                            "ipv6": (
                                tuple(reversed(node.originated_prefixes.ipv6))
                                if node.originated_prefixes.ipv6 is not None
                                else None
                            ),
                        }
                    )
                    if node.originated_prefixes is not None
                    else None
                ),
            }
        )
        for node in reversed(resolved.nodes)
    )
    domains = tuple(
        domain.model_copy(
            update={
                "node_ids": tuple(reversed(domain.node_ids)),
                "capabilities": tuple(reversed(domain.capabilities)),
            }
        )
        for domain in reversed(resolved.routing_domains)
    )
    rules = tuple(
        rule.model_copy(
            update={
                "endpoints": tuple(
                    endpoint.model_copy(update={"node_ids": tuple(reversed(endpoint.node_ids))})
                    for endpoint in reversed(rule.endpoints)
                )
            }
        )
        for rule in reversed(resolved.link_rules)
    )
    candidates = tuple(
        candidate.model_copy(
            update={
                "node_a": candidate.node_b,
                "node_b": candidate.node_a,
                "interface_a": candidate.interface_b,
                "interface_b": candidate.interface_a,
                "terminal_roles": tuple(reversed(candidate.terminal_roles)),
                "endpoint_segments": tuple(reversed(candidate.endpoint_segments)),
            }
        )
        for candidate in reversed(resolved.link_candidates)
    )
    sid_blocks = tuple(
        block.model_copy(update={"node_ids": tuple(reversed(block.node_ids))})
        for block in reversed(resolved.sid_blocks)
    )
    routing = resolved.routing.model_copy(
        update={
            "boundaries": tuple(
                boundary.model_copy(update={"export": tuple(reversed(boundary.export))})
                for boundary in reversed(resolved.routing.boundaries or ())
            )
        }
    )
    ephemeris = resolved.ephemeris.model_copy(
        update={
            "kernels": tuple(
                kernel.model_copy(update={"targets": tuple(reversed(kernel.targets))})
                for kernel in reversed(resolved.ephemeris.kernels)
            )
        }
    )
    reordered = resolved.model_copy(
        update={
            "nodes": nodes,
            "bodies": tuple(reversed(resolved.bodies)),
            "routing_domains": domains,
            "link_rules": rules,
            "link_candidates": candidates,
            "sid_blocks": sid_blocks,
            "routing": routing,
            "ephemeris": ephemeris,
        }
    )

    assert resolved_session_semantic_projection(reordered) == resolved_session_semantic_projection(
        resolved
    )


def test_projection_digest_changes_for_runtime_semantic_changes(resolved) -> None:
    baseline = resolved_session_semantic_digest(resolved)
    satellite = next(node for node in resolved.nodes if node.kind == "satellite")

    def replace_satellite(changed_satellite):
        return resolved.model_copy(
            update={
                "nodes": tuple(
                    changed_satellite if node.node_id == satellite.node_id else node
                    for node in resolved.nodes
                )
            }
        )

    terminal = satellite.terminal_inventory[0]
    changed_terminal = terminal.model_copy(update={"bandwidth_mbps": terminal.bandwidth_mbps + 1})
    changed_satellite = satellite.model_copy(
        update={"terminal_inventory": (changed_terminal, *satellite.terminal_inventory[1:])}
    )
    terminal_change = replace_satellite(changed_satellite)

    lo0 = satellite.interfaces.lo0
    addressing_change = replace_satellite(
        satellite.model_copy(
            update={
                "interfaces": satellite.interfaces.model_copy(
                    update={
                        "lo0": lo0.model_copy(update={"ipv4": "198.51.100.1/32"}),
                    }
                )
            }
        )
    )
    orbit_change = replace_satellite(
        satellite.model_copy(
            update={
                "orbit": satellite.orbit.model_copy(
                    update={"mean_anomaly_deg": satellite.orbit.mean_anomaly_deg + 1}
                )
            }
        )
    )

    sid_block = resolved.sid_blocks[0]
    sid_change = resolved.model_copy(
        update={
            "sid_blocks": (
                sid_block.model_copy(
                    update={
                        "sid_start": sid_block.sid_start + 1000,
                        "sid_end": sid_block.sid_end + 1000,
                    }
                ),
                *resolved.sid_blocks[1:],
            )
        }
    )

    domain = resolved.routing_domains[0]
    routing_domain_change = resolved.model_copy(
        update={
            "routing_domains": (
                domain.model_copy(update={"protocol": "ospf"}),
                *resolved.routing_domains[1:],
            )
        }
    )
    boundary = resolved.routing.boundaries[0]
    boundary_change = resolved.model_copy(
        update={
            "routing": resolved.routing.model_copy(
                update={
                    "boundaries": (
                        boundary.model_copy(update={"adapter": "bgp"}),
                        *resolved.routing.boundaries[1:],
                    )
                }
            )
        }
    )

    candidate = next(
        candidate for candidate in resolved.link_candidates if candidate.kind != "access"
    )
    interface_a, _interface_b = candidate.fixed_interfaces
    link_change = resolved.model_copy(
        update={
            "link_candidates": (
                candidate.model_copy(update={"interface_a": interface_a + "-changed"}),
                *(item for item in resolved.link_candidates if item is not candidate),
            )
        }
    )
    body = resolved.bodies[0]
    body_change = resolved.model_copy(
        update={
            "bodies": (
                body.model_copy(update={"mean_radius_km": body.mean_radius_km + 1}),
                *resolved.bodies[1:],
            )
        }
    )
    time_change = resolved.model_copy(
        update={
            "time": resolved.time.model_copy(update={"compression": resolved.time.compression + 1})
        }
    )
    ephemeris_kernel = resolved.ephemeris.kernels[0]
    ephemeris_change = resolved.model_copy(
        update={
            "ephemeris": resolved.ephemeris.model_copy(
                update={
                    "kernels": (
                        ephemeris_kernel.model_copy(update={"sha256": "0" * 64}),
                        *resolved.ephemeris.kernels[1:],
                    )
                }
            )
        }
    )

    for changed in (
        terminal_change,
        addressing_change,
        sid_change,
        routing_domain_change,
        boundary_change,
        link_change,
        body_change,
        orbit_change,
        time_change,
        ephemeris_change,
    ):
        assert resolved_session_semantic_digest(changed) != baseline


def test_canonical_json_and_cli_are_directly_diffable(resolved, capsys) -> None:
    compact = canonical_semantic_projection_json(resolved)
    assert json.loads(compact) == resolved_session_semantic_projection(resolved)
    assert "\n" not in compact

    assert main([str(SESSION), "--catalog-root", str(ROOT / "catalog" / "nodalarc")]) == 0
    cli_projection = json.loads(capsys.readouterr().out)
    assert cli_projection == resolved_session_semantic_projection(resolved)

    assert (
        main(
            [
                str(SESSION),
                "--catalog-root",
                str(ROOT / "catalog" / "nodalarc"),
                "--digest-only",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == resolved_session_semantic_digest(resolved)
