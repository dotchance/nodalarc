"""The NATS subject registry: one owner for every root, subject, wildcard and stream."""

from __future__ import annotations

import pytest
from nodalarc import nats_channels as nc


def test_every_session_builder_starts_with_a_declared_root():
    sid = "run-2026"
    assert nc.ome_visibility_subject(sid) == "nodalarc.ome.run-2026.visibility"
    assert nc.ome_all_subject(sid) == "nodalarc.ome.run-2026.>"
    assert nc.ome_all_subject() == "nodalarc.ome.>"
    assert nc.link_state_snapshot_subject(sid) == "nodalarc.links.run-2026.state"
    assert (
        nc.ground_link_decision_snapshot_subject(sid) == "nodalarc.links.run-2026.ground_decisions"
    )
    assert nc.session_ephemeris_subject(sid) == "nodalarc.session.run-2026.ephemeris"
    assert nc.scheduler_repair_subject(sid) == "nodalarc.scheduler.run-2026.repair"
    assert nc.probe_result_subject(sid) == "nodalarc.mi.run-2026.probe"
    assert nc.almanac_event_subject(sid) == "nodalarc.nodalpath.run-2026.almanac"
    assert nc.node_agent_subject("node02") == "nodalarc.agent.node02"
    assert nc.wiring_progress_subject("node02") == "nodalarc.agent.progress.node02"
    assert nc.wiring_progress_subscribe_subject() == "nodalarc.agent.progress.*"
    assert nc.debug_ctrl_subject("ome") == "nodalarc.logging.debug_ctrl.ome"
    assert nc.SUBJECT_PLAYBACK_CONTROL == "nodalarc.ome_control.playback"


@pytest.mark.parametrize(
    ("session_id", "tenant_id", "expected"),
    [
        ("", "", "nodalarc.ops._infra.ome.startup"),
        ("demo-36", "", "nodalarc.ops.demo-36.ome.startup"),
        ("", "acme", "nodalarc.ops.acme._tenant.ome.startup"),
        ("demo-36", "acme", "nodalarc.ops.acme.demo-36.ome.startup"),
    ],
)
def test_ops_and_debug_share_one_scope_hierarchy(session_id, tenant_id, expected):
    assert nc.ops_event_subject(session_id, "ome", "STARTUP", tenant_id=tenant_id) == expected
    assert nc.debug_event_subject(
        session_id, "ome", "STARTUP", tenant_id=tenant_id
    ) == expected.replace("nodalarc.ops.", "nodalarc.debug.", 1)


def test_event_subjects_sanitize_the_session_segment():
    assert nc.ops_event_subject("run.2026", "ome") == "nodalarc.ops.run-2026.ome"
    assert nc.ops_subscribe_subject("run.2026") == "nodalarc.ops.run-2026.>"


def test_subscribe_all_wildcards_cover_the_whole_root():
    assert nc.ops_subscribe_all_subject() == "nodalarc.ops.>"
    assert nc.debug_subscribe_all_subject() == "nodalarc.debug.>"
    assert nc.ops_subscribe_subject("", tenant_id="") == "nodalarc.ops._infra.>"


def test_deployed_stream_table_names_five_streams_with_their_roots():
    assert [s.name for s in nc.STREAMS] == [
        "NODALARC_OME",
        "NODALARC_LINKS",
        "NODALARC_SESSION",
        "NODALARC_OPS",
        "NODALARC_DEBUG",
    ]
    assert [s.subjects for s in nc.STREAMS] == [
        "nodalarc.ome.>",
        "nodalarc.links.>",
        "nodalarc.session.>",
        "nodalarc.ops.>",
        "nodalarc.debug.>",
    ]
    assert nc.STREAM_MI_EVENTS not in {s.name for s in nc.STREAMS}


def test_every_builder_output_lands_in_a_deployed_stream_or_a_declared_exception():
    sid = "run-2026"
    captured = [s.subjects[:-1] for s in nc.STREAMS]
    in_stream = [
        nc.ome_visibility_subject(sid),
        nc.ome_clock_subject(sid),
        nc.ome_heartbeat_subject(sid),
        nc.link_state_snapshot_subject(sid),
        nc.link_up_subject(sid),
        nc.actuation_state_subject(sid, "gs-1"),
        nc.session_ephemeris_subject(sid),
        nc.scheduling_checkpoint_subject(sid),
        nc.ops_event_subject(sid, "ome", "X"),
        nc.debug_event_subject(sid, "ome", "X"),
    ]
    for subject in in_stream:
        assert any(subject.startswith(root) for root in captured), subject
    # core request/reply and the undeployed integrations are outside every stream
    for subject in (
        nc.SUBJECT_PLAYBACK_CONTROL,
        nc.node_agent_subject("n"),
        nc.wiring_progress_subject("n"),
        nc.debug_ctrl_subject("ome"),
        nc.scenario_inject_subject(sid),
        nc.probe_result_subject(sid),
        nc.almanac_event_subject(sid),
    ):
        assert not any(subject.startswith(root) for root in captured), subject


def test_session_purge_filters_follow_the_stream_table_and_the_session_first_layout():
    filters = nc.session_purge_filters("run.2026")

    assert filters == (
        ("NODALARC_OME", "nodalarc.ome.run-2026.>"),
        ("NODALARC_LINKS", "nodalarc.links.run-2026.>"),
        ("NODALARC_SESSION", "nodalarc.session.run-2026.>"),
        ("NODALARC_OPS", "nodalarc.ops.run-2026.>"),
        ("NODALARC_DEBUG", "nodalarc.debug.run-2026.>"),
    )
    assert all(nc.ops_event_subject("run.2026", "ome").startswith(f[:-1]) for _, f in filters[3:4])


def test_session_purge_filters_refuse_a_tenant_scope():
    with pytest.raises(nc.TenantScopeUnsupported, match="tenant_id='acme'"):
        nc.session_purge_filters("run-2026", tenant_id="acme")


def test_session_purge_filters_never_purge_without_a_session():
    with pytest.raises(ValueError):
        nc.session_purge_filters("")


_ROOT_BUILDERS = {
    "ROOT_OME": (
        lambda: nc.ome_all_subject(),
        lambda: nc.ome_all_subject("s"),
        lambda: nc.ome_clock_subject("s"),
    ),
    "ROOT_LINKS": (lambda: nc.link_up_subject("s"), lambda: nc.actual_links_subscribe_subject("s")),
    "ROOT_SESSION": (lambda: nc.playback_state_subject("s"),),
    "ROOT_SCHEDULER": (lambda: nc.scenario_inject_subject("s"),),
    "ROOT_OPS": (
        lambda: nc.ops_event_subject("s", "ome"),
        lambda: nc.ops_subscribe_subject(""),
        lambda: nc.ops_subscribe_all_subject(),
    ),
    "ROOT_DEBUG": (
        lambda: nc.debug_event_subject("", "ome"),
        lambda: nc.debug_subscribe_all_subject(),
    ),
    "ROOT_MI": (lambda: nc.adapter_event_subject("s"),),
    "ROOT_NODALPATH": (lambda: nc.almanac_event_subject("s"),),
    "ROOT_AGENT": (
        lambda: nc.node_agent_subject("n"),
        lambda: nc.wiring_progress_subject("n"),
        lambda: nc.wiring_progress_subscribe_subject(),
    ),
    "ROOT_LOGGING": (lambda: nc.debug_ctrl_subject("ome"),),
}


@pytest.mark.parametrize("root_name", sorted(_ROOT_BUILDERS))
def test_changing_a_declared_root_moves_every_builder_under_it(monkeypatch, root_name):
    """A builder still carrying its own literal would stay behind when the root changes."""
    monkeypatch.setattr(nc, root_name, "moved.root")

    for build in _ROOT_BUILDERS[root_name]:
        assert build().startswith("moved.root."), build()


def test_stream_table_and_purge_filters_follow_the_roots(monkeypatch):
    assert [s.subjects for s in nc.STREAMS] == [
        f"{r}.>" for r in (nc.ROOT_OME, nc.ROOT_LINKS, nc.ROOT_SESSION, nc.ROOT_OPS, nc.ROOT_DEBUG)
    ]
    assert nc.session_purge_filters("s") == tuple(
        (s.name, f"{s.subjects[:-1]}s.>") for s in nc.STREAMS
    )


def _chart_acl_lists() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """The publish and subscribe lists the chart renders today, keyed by values user key."""
    import re
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2] / "deploy/helm/templates/nats-configmap.yaml"
    ).read_text()
    found: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for block in re.finditer(
        r"user: \{\{ \.Values\.nats\.auth\.users\.(\w+)\.username \| quote \}\}.*?"
        r"publish: \[(.*?)\],\s*subscribe: \[(.*?)\]",
        text,
        re.S,
    ):
        key, publish, subscribe = block.groups()
        parse = lambda s: tuple(
            dict.fromkeys(item.strip().strip('"') for item in s.split(",") if item.strip())
        )
        found[key] = (parse(publish), parse(subscribe))
    return found


def test_acl_inventory_reproduces_the_chart_policy_as_it_stands():
    """Until the chart renders from the registry, the registry must equal the chart, entry for entry."""
    chart = _chart_acl_lists()
    assert (
        set(chart)
        == {user.key for user in nc.NATS_USERS}
        == {"admin", "scheduler", "nodeAgent", "service"}
    )
    for user in nc.NATS_USERS:
        assert user.publish == chart[user.key][0], user.key
        assert user.subscribe == chart[user.key][1], user.key


def test_every_acl_pattern_is_a_registry_root_or_nats_infrastructure():
    roots = (
        nc.ROOT_OME,
        nc.ROOT_LINKS,
        nc.ROOT_SESSION,
        nc.ROOT_SCHEDULER,
        nc.ROOT_OPS,
        nc.ROOT_DEBUG,
        nc.ROOT_MI,
        nc.ROOT_NODALPATH,
        nc.ROOT_AGENT,
        nc.ROOT_OME_CONTROL,
    )
    for user in nc.NATS_USERS:
        for pattern in user.publish + user.subscribe:
            assert pattern in ("$JS.API.>", "_INBOX.>", "nodalarc.>") or any(
                pattern.startswith(f"{root}.") for root in roots
            ), (user.key, pattern)
