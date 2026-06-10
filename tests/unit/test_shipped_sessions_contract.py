# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Every shipped session must WORK, not merely parse.

The shipped catalog sessions are the worked examples users copy to build
their own; a session that resolves but cannot route (disconnected domains,
gateways that can never schedule, readiness errors) teaches the wrong
lesson and burns trust. This contract runs the full deploy-time gate over
every session under catalog/nodalarc/sessions/:

- resolves through the production resolver (typed failures are real bugs),
- zero readiness errors,
- zero W005 impossible-geometry warnings (every gateway can actually see
  the constellation its access rule pairs it with — the Fairbanks class),
- every routing domain is ONE connected component over link candidates
  plus site-LAN adjacency,
- every access rule contributes at least one candidate.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.session_validator import validate_session_readiness

SESSIONS_DIR = Path(__file__).resolve().parents[2] / "catalog" / "nodalarc" / "sessions"
SESSION_PATHS = sorted(SESSIONS_DIR.glob("*.yaml"))


def _resolved(path: Path):
    return load_session_resolution_from_file(
        path, origin="test.shipped_sessions", run_id="run-test-0042"
    ).resolved


def test_session_inventory_is_nonempty() -> None:
    assert SESSION_PATHS, f"no shipped sessions found under {SESSIONS_DIR}"


@pytest.mark.parametrize("path", SESSION_PATHS, ids=lambda p: p.stem)
def test_shipped_session_passes_full_readiness_gate(path: Path) -> None:
    resolved = _resolved(path)

    findings = validate_session_readiness(resolved, available_node_count=3)
    errors = [f for f in findings if f.level == "error"]
    assert errors == [], f"{path.name}: readiness errors: {[f.message for f in errors]}"

    impossible = [f for f in findings if f.code == "W005"]
    assert impossible == [], (
        f"{path.name}: shipped content pairs gateways with constellations they "
        f"can never see: {[f.message for f in impossible]}"
    )


@pytest.mark.parametrize("path", SESSION_PATHS, ids=lambda p: p.stem)
def test_shipped_session_domains_are_single_components(path: Path) -> None:
    resolved = _resolved(path)

    lan_members: dict[str, list[str]] = defaultdict(list)
    for node in resolved.nodes:
        if node.kind != "satellite":
            lan_members[node.namespace].append(node.node_id)

    for domain in resolved.routing_domains:
        members = set(domain.node_ids)
        adjacency: dict[str, set[str]] = defaultdict(set)
        for candidate in resolved.link_candidates:
            if candidate.node_a in members and candidate.node_b in members:
                adjacency[candidate.node_a].add(candidate.node_b)
                adjacency[candidate.node_b].add(candidate.node_a)
        for ids in lan_members.values():
            local = [node_id for node_id in ids if node_id in members]
            for i, left in enumerate(local):
                for right in local[i + 1 :]:
                    adjacency[left].add(right)
                    adjacency[right].add(left)

        seen: set[str] = set()
        components = 0
        for node_id in sorted(members):
            if node_id in seen:
                continue
            components += 1
            stack = [node_id]
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(adjacency[current] - seen)
        assert components == 1, (
            f"{path.name}: domain {domain.domain_id} resolves to {components} "
            "disconnected components - traffic cannot route across them"
        )


@pytest.mark.parametrize("path", SESSION_PATHS, ids=lambda p: p.stem)
def test_shipped_session_access_rules_all_produce_candidates(path: Path) -> None:
    resolved = _resolved(path)
    access_rules = {r.rule_id for r in resolved.link_rules if r.kind == "access" and r.enabled}
    rules_with_candidates = {c.rule_id for c in resolved.link_candidates if c.kind == "access"}
    silent = sorted(access_rules - rules_with_candidates)
    assert silent == [], f"{path.name}: access rules with zero candidates: {silent}"


def test_w005_catches_the_fairbanks_class() -> None:
    """Regression: a 64.8 N site under a 53-degree ring with a 25-degree mask
    must be flagged as impossible. This exact content shipped and produced a
    gateway that deployed, wired, and could never schedule a link."""
    import yaml

    session_path = SESSIONS_DIR / "earth-leo-heo-geo-luna-reachability.yaml"
    raw = yaml.safe_load(session_path.read_text())
    set_path = (
        Path(__file__).resolve().parents[2]
        / "catalog/nodalarc/site-sets/earth/leo/earth-leo-starlink-gateway-sites.yaml"
    )
    site_set = yaml.safe_load(set_path.read_text())
    site_set["site_set"]["sites"].append("nodalarc:sites/earth/us/ak/earth-us-ak-fairbanks.yaml")
    for segment in raw["segments"]:
        placement = segment.get("placement")
        if placement and "starlink" in str(placement.get("from_site_set", "")):
            placement["from_site_set"] = site_set

    from nodalarc.models.resolved_session import SourceContext
    from nodalarc.resolve_session import default_catalog_roots, resolve_session

    resolved = resolve_session(
        raw,
        catalog_roots=default_catalog_roots(),
        source_context=SourceContext(origin="test.w005", run_id="run-test-0043"),
    )
    findings = validate_session_readiness(resolved, available_node_count=3)
    fairbanks = [f for f in findings if f.code == "W005" and "fairbanks" in f.message]
    assert fairbanks, "re-adding Fairbanks must trip the impossible-geometry check"
