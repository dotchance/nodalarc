# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.session_validator import validate_session_readiness

from tests.catalog_session_fixtures import shipped_read_view

SESSIONS_DIR = Path(__file__).resolve().parents[3] / "catalog" / "nodalarc" / "sessions"
SESSION_PATHS = sorted(SESSIONS_DIR.glob("*.yaml"))


@pytest.fixture(scope="module", params=SESSION_PATHS, ids=lambda p: p.stem)
def shipped_session(request):
    path = request.param
    resolved = load_session_resolution_from_file(
        path, origin="test.shipped_sessions", run_id="run-test-0042", catalog=shipped_read_view()
    ).resolved
    return path, resolved


def test_shipped_session_passes_full_readiness_gate(shipped_session) -> None:
    path, resolved = shipped_session

    findings = validate_session_readiness(resolved, available_node_count=3)
    errors = [f for f in findings if f.level == "error"]
    assert errors == [], f"{path.name}: readiness errors: {[f.message for f in errors]}"

    impossible = [f for f in findings if f.code == "W005"]
    assert impossible == [], (
        f"{path.name}: shipped content pairs gateways with constellations they "
        f"can never see: {[f.message for f in impossible]}"
    )


def _shipped_step_context(path: Path):
    """A shipped session's OME config and the step context OME builds from it."""
    from ome.event_stream import build_step_context
    from ome.main import _effective_ground_scheduling_for_runtime, _load_session_config

    cfg = _load_session_config(str(path), run_id="run-shipped-smoke-0001")
    ctx = build_step_context(
        long_delay_access_pairs=cfg.long_delay_access_pairs,
        assigned_space_pairs=cfg.assigned_space_pairs,
        ground_pair_pools=cfg.ground_pair_pools,
        ground_rule_pools=cfg.ground_rule_pools,
        rule_n=cfg.rule_n,
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
        physics=cfg.physics,
        active_bodies=cfg.active_bodies,
    )
    return cfg, ctx


def _assert_tick_round_trips(cfg, ctx, result, step: int, step_seconds: float):
    """OME builds its snapshot leaves without validation; validating the dump
    of what a real tick builds, as OME's publisher builds it, must reproduce it
    exactly. Returns the tick's link state snapshot."""
    from nodalarc.models.link_decisions import GroundLinkDecisionSnapshot
    from nodalarc.models.link_state import LinkStateSnapshot
    from ome.snapshot_builder import DeferredLinkDecisionSnapshot, DeferredLinkStateSnapshot

    link_snapshot = DeferredLinkStateSnapshot(
        source=result.link_snapshot_source,
        interface_map=cfg.interface_map,
        terminal_interface_names=cfg.terminal_interface_names,
        handover_modes=ctx.gs_handover_modes,
        mbb_overlap_ticks=ctx.gs_mbb_overlap_ticks,
        rule_map=cfg.rule_map,
        sim_time=result.sim_time,
        seq=step,
        interval_s=step_seconds,
        epoch_id=0,
        current_step=result.step,
    ).build()
    wire = link_snapshot.model_dump_json()
    assert LinkStateSnapshot.model_validate_json(wire).model_dump_json() == wire
    decision_snapshot = DeferredLinkDecisionSnapshot(
        decisions=result.ground_decisions,
        unscheduled_pairs=result.ground_unscheduled_pairs,
        policy_audit=result.ground_allocation.policy_audit,
        allocation_events=result.ground_allocation.allocation_events,
        sim_time=result.sim_time,
        snapshot_seq=step,
        epoch_id=0,
    ).build()
    wire = decision_snapshot.model_dump_json()
    assert GroundLinkDecisionSnapshot.model_validate_json(wire).model_dump_json() == wire
    return link_snapshot


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
    from nodalarc.models.session import session_epoch_unix
    from ome.event_stream import compute_step

    cfg, ctx = _shipped_step_context(path)
    epoch_unix = session_epoch_unix(cfg.resolved.time)
    step_seconds = float(cfg.resolved.time.step_seconds)

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
        propagated_nodes.update(result.propagated_states)
        _assert_tick_round_trips(cfg, ctx, result, step, step_seconds)

    sat_count = len(cfg.satellites)
    assert len(propagated_nodes) == sat_count, (
        f"{path.name}: {sat_count} satellites resolved but only "
        f"{len(propagated_nodes)} ever propagated"
    )


def test_space_pairs_of_a_visible_candidates_rule_are_chosen_on_assigned_terminals() -> None:
    """mars-ion-relay-network joins its Mars orbiters with a ``visible_candidates`` rule. Its
    pairs carry no fixed interface: at each tick the allocator links every usable one that
    fits its nodes' terminals, on a terminal of the mount the rule selects at each node, and
    the static fixed-link scheduler never sees them."""
    from nodalarc.models.session import session_epoch_unix
    from ome.event_stream import compute_step

    cfg, ctx = _shipped_step_context(SESSIONS_DIR / "mars-ion-relay-network.yaml")
    space_pairs = {item.pair: item for item in ctx.assigned_space_pairs}
    assert space_pairs, "the session resolves no assigned pair between space nodes"
    assert all(item.rule_id == "mars_relay_crosslink" for item in space_pairs.values())
    assert not set(space_pairs) & set(cfg.interface_map)

    epoch_unix = session_epoch_unix(cfg.resolved.time)
    step_seconds = float(cfg.resolved.time.step_seconds)
    isl_state: dict = {}
    gs_state: dict = {}
    associations: dict = {}
    teardowns: dict = {}
    linked: set[tuple[str, str]] = set()
    for step in range(31):
        result = compute_step(
            ctx, epoch_unix, step, step_seconds, 0.0, isl_state, gs_state, associations, teardowns
        )
        associations = result.associations
        teardowns = result.pending_teardowns
        assert not set(space_pairs) & set(result.isl_links)
        snapshot = _assert_tick_round_trips(cfg, ctx, result, step, step_seconds)
        rows = {(link.node_a, link.node_b): link for link in snapshot.links}
        for pair, item in space_pairs.items():
            if pair not in result.ground_allocation.scheduled_pairs:
                continue
            linked.add(pair)
            index_a, index_b = result.associations[pair]
            assert index_a in item.end_a.terminal_indices
            assert index_b in item.end_b.terminal_indices
            row = rows[pair]
            assert row.carrier == "UP"
            assert (row.terminal_index_a, row.terminal_index_b) == (index_a, index_b)
            assert row.interface_a == cfg.terminal_interface_names[pair[0]][index_a]
            assert row.interface_b == cfg.terminal_interface_names[pair[1]][index_b]
            assert row.link_rule_id == "mars_relay_crosslink"
    assert linked, "no pair between the Mars orbiters linked in thirty ticks"


def test_a_make_before_break_teardown_tick_round_trips() -> None:
    """The round trip above never meets a teardown in thirty ticks. earth-leo-simple
    hands over make-before-break; run it to its first pending teardown and check
    that tick's snapshot carries the teardown and survives the round trip."""
    from nodalarc.models.session import session_epoch_unix
    from ome.event_stream import compute_step

    cfg, ctx = _shipped_step_context(SESSIONS_DIR / "earth-leo-simple.yaml")
    epoch_unix = session_epoch_unix(cfg.resolved.time)
    step_seconds = float(cfg.resolved.time.step_seconds)
    isl_state: dict = {}
    gs_state: dict = {}
    dwell_state: dict = {}
    associations: dict = {}
    teardowns: dict = {}
    for step in range(300):
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
        if teardowns:
            break
    else:
        pytest.fail("earth-leo-simple reached no make-before-break teardown in 300 ticks")
    snapshot = _assert_tick_round_trips(cfg, ctx, result, step, step_seconds)
    teardown_links = [link for link in snapshot.links if link.scheduling_state == "teardown"]
    assert teardown_links
    assert all(link.teardown_remaining_ticks is not None for link in teardown_links)
    assert all(link.topology_mode is not None for link in snapshot.links)


def test_shipped_session_renders_every_node_through_its_adapter(shipped_session) -> None:
    """Every node of every shipped session must reach the deploy-time
    render stage. The geo sessions deployed zero times after the resolver
    cutover because rendering hard-required plane/slot — grid coordinates
    that individually placed satellites (GEO longitude slots) legitimately
    lack — and nothing exercised the render stage until a live
    `make session`. This closes that gap for the whole catalog: resolve AND
    render through each node's own adapter, not merely resolve."""
    from nodalarc.workloads.adapter import SessionContext
    from nodalarc_operator.session_deployer import _pod_inventory

    from adapters.registry import adapter_named

    path, _resolved = shipped_session
    resolution = load_session_resolution_from_file(
        path, origin="test.shipped_sessions", run_id="run-test-0042", catalog=shipped_read_view()
    )
    resolved = resolution.resolved
    context = SessionContext(resolved)
    rendered = 0
    for node in resolved.nodes:
        adapter = adapter_named(resolution.workload_profiles[node.profile].adapter)
        if adapter is None:
            # A profile without an adapter is self-describing; nothing renders.
            continue
        files = adapter.render_node(node, context).files
        assert files, f"{path.name}: {node.node_id} rendered no configuration"
        rendered += 1
    assert rendered, f"{path.name}: no node rendered adapter configuration"

    inventory = _pod_inventory(resolved)
    for node in resolved.nodes:
        if node.placement_frame == "orbit" and (node.plane is None or node.slot is None):
            assert "plane" not in inventory[node.node_id], (
                f"{path.name}: non-grid satellite {node.node_id} must not "
                "carry fabricated grid coordinates"
            )
            assert "slot" not in inventory[node.node_id]


def test_shipped_session_domains_are_single_components(shipped_session) -> None:
    path, resolved = shipped_session

    lan_members: dict[str, list[str]] = defaultdict(list)
    for node in resolved.nodes:
        if node.placement_frame != "orbit":
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


def test_shipped_session_access_rules_all_produce_candidates(shipped_session) -> None:
    path, resolved = shipped_session
    access_rules = {r.rule_id for r in resolved.link_rules if r.kind == "access" and r.enabled}
    rules_with_candidates = {c.rule_id for c in resolved.link_candidates if c.kind == "access"}
    silent = sorted(access_rules - rules_with_candidates)
    assert silent == [], f"{path.name}: access rules with zero candidates: {silent}"


def test_w005_catches_the_fairbanks_class(tmp_path: Path) -> None:
    """Regression: a 64.8 N site under a 53-degree ring with a 25-degree mask
    must be flagged as impossible. This exact content shipped and produced a
    gateway that deployed, wired, and could never schedule a link."""
    import yaml

    session_path = SESSIONS_DIR / "earth-leo-heo-geo-luna-reachability.yaml"
    raw = load_configuration_yaml(session_path.read_bytes())
    set_path = (
        Path(__file__).resolve().parents[3]
        / "catalog/nodalarc/site-sets/earth/leo/earth-leo-starlink-gateway-sites.yaml"
    )
    site_set = load_configuration_yaml(set_path.read_bytes())
    site_set["site_set"]["id"] = "test-fairbanks"
    site_set["site_set"]["sites"].append("nodalarc:sites/earth/us/ak/earth-us-ak-fairbanks.yaml")
    user_root = tmp_path / "catalog" / "user"
    user_site_set = user_root / "site-sets" / "test-fairbanks.yaml"
    user_site_set.parent.mkdir(parents=True)
    user_site_set.write_text(yaml.safe_dump(site_set, sort_keys=False), encoding="utf-8")
    for segment in raw["segments"]:
        placement = segment.get("placement")
        if placement and "starlink" in str(placement.get("from_site_set", "")):
            placement["from_site_set"] = "user:site-sets/test-fairbanks.yaml"

    from nodalarc.catalog_paths import CatalogRoots
    from nodalarc.models.resolved_session import SourceContext
    from nodalarc.resolve_session import resolve_session

    resolved = resolve_session(
        raw,
        catalog=FilesystemCatalogReadView(
            CatalogRoots.from_catalog_root(
                Path("catalog/nodalarc"),
                user_root=user_root,
            )
        ),
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
        "earth-leo-polar-seam": 1,
        "earth-leo-polar-seam-tracking": 1,
        "earth-leo-walker": 1,
        "earth-meo-gps": 1,
        "earth-leo-heo-geo-luna-reachability": 1,
        "earth-luna-dtn": 1,
        "earth-luna-dtn-custody": 1,
        "earth-luna-quic": 1,
        "earth-mars-hdtn-relay": 1,
        "mars-ion-relay-network": 1,
        "earth-luna-mars-dtn": 1,
        # GEO-only sessions: near-stationary geometry, deliberate 10 s tick.
        "earth-geo-inmarsat": 10,
        "earth-geo-tdrs": 10,
    }

    def test_every_shipped_session_matches_declared_tick(self):
        from pathlib import Path

        sessions_dir = Path(__file__).resolve().parents[3] / "catalog/nodalarc/sessions"
        seen = {}
        for path in sorted(sessions_dir.glob("*.yaml")):
            data = load_configuration_yaml(path.read_bytes())
            seen[path.stem] = data["time"]["step_seconds"]
        assert seen == self.DECLARED_TICKS, (
            "shipped-session tick rates diverged from the declared table — "
            "tick changes are owner decisions, update both deliberately"
        )

    def test_dwell_horizons_preserve_two_hour_intent_at_one_hz(self):
        from pathlib import Path

        sessions_dir = Path(__file__).resolve().parents[3] / "catalog/nodalarc/sessions"
        for path in sorted(sessions_dir.glob("*.yaml")):
            data = load_configuration_yaml(path.read_bytes())
            step = data["time"]["step_seconds"]
            text = path.read_text()
            for match in __import__("re").finditer(r"lookahead_horizon_ticks: (\d+)", text):
                horizon_s = int(match.group(1)) * step
                assert horizon_s == 7200, (
                    f"{path.name}: dwell lookahead is {horizon_s}s of sim time; "
                    "the authored intent is 2 hours — scale ticks with the step"
                )


def test_a_mount_drawn_on_by_a_fixed_rule_and_an_assigned_rule_is_refused() -> None:
    """mars-ion-relay-network's ``mars_relay_crosslink`` assigns the relays' ``relay_x``
    terminals per pass. A rule that fixes a pair on the same mount would bind one of those
    terminals to one peer while it is also handed to passes; the session is refused naming
    the node, the mount and both rules."""
    from nodalarc.resolve_session import resolve_session
    from nodalarc.runtime_support import UnsupportedFeatureError

    raw = load_configuration_yaml((SESSIONS_DIR / "mars-ion-relay-network.yaml").read_text())
    crosslink = {"all": [{"role": "crosslink"}, {"medium": "rf"}, {"mount": "relay_x"}]}
    raw["link_rules"].append(
        {
            "id": "lmo_pair",
            "topology": {"mode": "explicit_pairs", "pairs": [{"a": "mro", "b": "tgo"}]},
            "endpoints": [
                {
                    "select": {"all": [{"segment": "mars_relays"}, {"node": "mro"}]},
                    "terminal": crosslink,
                },
                {
                    "select": {"all": [{"segment": "mars_relays"}, {"node": "tgo"}]},
                    "terminal": crosslink,
                },
            ],
        }
    )

    with pytest.raises(UnsupportedFeatureError) as refusal:
        resolve_session(raw, catalog=shipped_read_view())

    values = {feature.value for feature in refusal.value.features}
    assert values == {"mount_bound_and_assigned"}
    message = " ".join(feature.message for feature in refusal.value.features)
    assert (
        "'relay_x'" in message and "'lmo_pair'" in message and "'mars_relay_crosslink'" in message
    )


def _steps_of_a_changed_session(tmp_path: Path, change, steps: int = 31):
    """Run a shipped session with one change through OME's real step loop; yields each tick's
    context and result."""
    import yaml
    from nodalarc.models.session import session_epoch_unix
    from ome.event_stream import compute_step

    raw = load_configuration_yaml((SESSIONS_DIR / "earth-leo-simple.yaml").read_text())
    change(raw)
    path = tmp_path / "earth-leo-simple.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg, ctx = _shipped_step_context(path)
    epoch_unix = session_epoch_unix(cfg.resolved.time)
    step_seconds = float(cfg.resolved.time.step_seconds)
    isl_state: dict = {}
    gs_state: dict = {}
    associations: dict = {}
    teardowns: dict = {}
    for step in range(steps):
        result = compute_step(
            ctx, epoch_unix, step, step_seconds, 0.0, isl_state, gs_state, associations, teardowns
        )
        associations = result.associations
        teardowns = result.pending_teardowns
        _assert_tick_round_trips(cfg, ctx, result, step, step_seconds)
        yield ctx, result


def _ground_segment_of(raw: dict) -> dict:
    return next(segment for segment in raw["segments"] if "placement" in segment)


def test_stations_that_state_no_scheduling_are_linked_by_nodalarcs_default(tmp_path) -> None:
    """With no scheduling stated, no station is a stating node and none has a policy; the
    default chooser still links each station to satellites it can see."""

    def change(raw: dict) -> None:
        del _ground_segment_of(raw)["apply"]["scheduling"]

    linked: set[str] = set()
    for ctx, result in _steps_of_a_changed_session(tmp_path, change):
        assert ctx.stating_nodes == frozenset()
        assert not ctx.gs_selection_policies and not ctx.gs_handover_modes
        for pair in result.ground_allocation.scheduled_pairs:
            linked.update(node for node in pair if node in ctx.gs_positions)
    assert linked, "no station was linked in thirty ticks"


def test_a_rule_that_states_n_holds_each_node_to_n_links(tmp_path) -> None:
    """``n: 1`` on the access rule: no station and no satellite holds more than one link of
    the rule at once, whatever number of terminals it has."""

    def change(raw: dict) -> None:
        del _ground_segment_of(raw)["apply"]["scheduling"]
        access = next(rule for rule in raw["link_rules"] if rule["id"] == "leo_access")
        access["topology"] = {"mode": "visible_candidates", "n": 1}

    linked = 0
    for ctx, result in _steps_of_a_changed_session(tmp_path, change):
        assert ctx.rule_n == {"leo_access": 1}
        held: dict[str, int] = defaultdict(int)
        for pair in result.ground_allocation.scheduled_pairs:
            if pair in result.pending_teardowns:
                continue
            for node in pair:
                held[node] += 1
        assert all(count <= 1 for count in held.values()), dict(held)
        linked += len(held)
    assert linked, "no link of the rule came up in thirty ticks"
