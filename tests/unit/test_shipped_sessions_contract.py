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
def test_shipped_session_computes_real_steps(path: Path) -> None:
    """Every shipped session must RUN, not merely resolve and render.

    The sessions are worked examples of the primitives — the product is
    the primitives, and an engine change that computes correctly for one
    session while breaking another must fail in the suite run before
    commit, not on a live deploy. (The geo sessions were undeployable
    for weeks because only one session shape ever got exercised; the
    render contract closed the deploy-time gap, this closes the runtime
    one.) Thirty real ticks of the production compute loop per session:
    propagation across every body in the session, visibility,
    allocation, dwell where authored, event diffing — the full per-tick
    pipeline, no mocks.
    """
    from nodalarc.models.session import resolve_session_epoch
    from ome.event_stream import build_step_context, compute_step
    from ome.main import _effective_ground_scheduling_for_runtime, _load_session_config

    cfg = _load_session_config(str(path), run_id="run-shipped-smoke-0001")
    ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )
    epoch_unix = resolve_session_epoch(cfg.resolved.time)
    step_seconds = int(cfg.resolved.time.step_seconds)

    isl_state: dict = {}
    gs_state: dict = {}
    dwell_state: dict = {}
    associations: dict = {}
    teardowns: dict = {}
    propagated_nodes: set[str] = set()
    for step in range(31):
        result = compute_step(
            ctx,
            epoch_unix,
            step,
            step_seconds,
            0.0,
            isl_state,
            gs_state,
            associations,
            teardowns,
            dwell_state=dwell_state,
        )
        associations = result.associations
        teardowns = result.pending_teardowns
        propagated_nodes.update(result.link_snapshot_source.propagated_states)

    sat_count = len(cfg.satellites)
    assert len(propagated_nodes) == sat_count, (
        f"{path.name}: {sat_count} satellites resolved but only "
        f"{len(propagated_nodes)} ever propagated"
    )


@pytest.mark.parametrize("path", SESSION_PATHS, ids=lambda p: p.stem)
def test_shipped_session_renders_template_vars_for_every_node(path: Path) -> None:
    """Every node of every shipped session must reach the deploy-time
    render stage. The geo sessions deployed zero times after the resolver
    cutover because template-vars building hard-required plane/slot —
    grid coordinates that individually placed satellites (GEO longitude
    slots) legitimately lack — and nothing exercised the render stage
    until a live `make session`. This closes that gap for the whole
    catalog: resolve AND render, not merely resolve."""
    from nodalarc.template_vars import build_template_vars_from_resolved

    resolved = _resolved(path)
    for node in resolved.nodes:
        result = build_template_vars_from_resolved(resolved, node.node_id)
        assert result["node_id"] == node.node_id
        if node.kind == "satellite" and (node.plane is None or node.slot is None):
            assert "plane" not in result and "slot" not in result, (
                f"{path.name}: non-grid satellite {node.node_id} must not "
                "carry fabricated grid coordinates"
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


class TestTickRateContract:
    """The system tick is 1 Hz; deviations are deliberate, per-session,
    and pinned here. A 10x tick change once rode silently inside a
    configuration refactor, rewrote the meaning of every tick-denominated
    policy field, and masked an engine regression — any future change to
    these values must arrive as a loud reviewed diff of this table.
    """

    DECLARED_TICKS = {
        "earth-leo-simple": 1,
        "earth-leo-polar": 1,
        "earth-leo-walker": 1,
        "earth-meo-gps": 1,
        "earth-leo-heo-geo-luna-reachability": 1,
        # GEO-only sessions: near-stationary geometry, deliberate 10 s tick.
        "earth-geo-inmarsat": 10,
        "earth-geo-tdrs": 10,
    }

    def test_every_shipped_session_matches_declared_tick(self):
        from pathlib import Path

        import yaml

        sessions_dir = Path(__file__).resolve().parents[2] / "catalog/nodalarc/sessions"
        seen = {}
        for path in sorted(sessions_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            seen[path.stem] = data["time"]["step_seconds"]
        assert seen == self.DECLARED_TICKS, (
            "shipped-session tick rates diverged from the declared table — "
            "tick changes are owner decisions, update both deliberately"
        )

    def test_dwell_horizons_preserve_two_hour_intent_at_one_hz(self):
        from pathlib import Path

        import yaml

        sessions_dir = Path(__file__).resolve().parents[2] / "catalog/nodalarc/sessions"
        for path in sorted(sessions_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            step = data["time"]["step_seconds"]
            text = path.read_text()
            for match in __import__("re").finditer(r"lookahead_horizon_ticks: (\d+)", text):
                horizon_s = int(match.group(1)) * step
                assert horizon_s == 7200, (
                    f"{path.name}: dwell lookahead is {horizon_s}s of sim time; "
                    "the authored intent is 2 hours — scale ticks with the step"
                )
