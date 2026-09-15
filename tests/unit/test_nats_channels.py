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


def test_chart_templates_author_no_subject_patterns():
    """The chart consumes the rendered inventory; it names no stream, subject or ACL pattern itself."""
    import re
    from pathlib import Path

    templates = Path(__file__).resolve().parents[2] / "deploy/helm/templates"
    for path in sorted(templates.glob("*.yaml")):
        text = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        assert not re.search(
            r"nodalarc\.(ome|links|session|scheduler|ops|debug|mi|nodalpath|agent|ome_control|logging)[.>*\"]",
            text,
        ), path.name
        assert not re.search(r"\bNODALARC_(OME|LINKS|SESSION|OPS|DEBUG|MI)\b", text), path.name
        assert "nodalarc-nats:4222" not in text, path.name


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


def test_nats_url_comes_from_the_environment_only(monkeypatch):
    monkeypatch.setenv("NODALARC_NATS_URL", " nats://user:pw@nats.example:4333 ")
    assert nc.nats_url() == "nats://user:pw@nats.example:4333"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nats_url_refuses_an_unset_or_blank_variable(monkeypatch, value):
    """The suite-wide environment fixture must not conceal this boundary: cleared here."""
    if value is None:
        monkeypatch.delenv("NODALARC_NATS_URL", raising=False)
    else:
        monkeypatch.setenv("NODALARC_NATS_URL", value)

    with pytest.raises(RuntimeError, match="NODALARC_NATS_URL is not set"):
        nc.nats_url()


def test_platform_config_carries_no_nats_url():
    from nodalarc.platform_config import PlatformConfig

    assert "nats_url" not in PlatformConfig.model_fields


def test_messaging_inventory_is_the_stream_and_user_tables():
    import yaml

    rendered = yaml.safe_load(nc.render_messaging_inventory())

    assert rendered == nc.messaging_inventory()
    assert [s["name"] for s in rendered["streams"]] == [s.name for s in nc.STREAMS]
    assert [s["subjects"] for s in rendered["streams"]] == [s.subjects for s in nc.STREAMS]
    assert [u["key"] for u in rendered["users"]] == [u.key for u in nc.NATS_USERS]
    for user, row in zip(nc.NATS_USERS, rendered["users"], strict=True):
        assert tuple(row["publish"]) == user.publish
        assert tuple(row["subscribe"]) == user.subscribe


def test_renderer_command_writes_the_inventory_to_stdout():
    import os
    import subprocess
    import sys
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "lib")}
    out = subprocess.run(
        [sys.executable, "-m", "nodalarc.nats_channels", "--render-messaging"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
    ).stdout

    assert yaml.safe_load(out) == nc.messaging_inventory()
    refused = subprocess.run(
        [sys.executable, "-m", "nodalarc.nats_channels"],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
    )
    assert refused.returncode != 0


def test_chart_retention_is_keyed_by_the_deployed_stream_names():
    """Retention is deployment configuration; it must name exactly the registry's streams."""
    from pathlib import Path

    import yaml

    values = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "deploy/helm/values.yaml").read_text()
    )
    retention = values["nats"]["streamRetention"]

    assert set(retention) == {s.name for s in nc.STREAMS}
    for name, entry in retention.items():
        assert set(entry) == {"maxMsgsPerSubject", "maxAge", "maxBytes"}, name


def _inventory_with(**changes):
    import copy

    inventory = copy.deepcopy(nc.messaging_inventory())
    for path, value in changes.items():
        target = inventory
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[int(key)] if key.isdigit() else target[key]
        last = keys[-1]
        if last.isdigit():
            target[int(last)] = value
        else:
            target[last] = value
    return inventory


def test_producer_validation_accepts_the_registry_tables():
    nc.validate_messaging_inventory(nc.messaging_inventory())


@pytest.mark.parametrize(
    ("changes", "label"),
    [
        ({"streams": []}, "no streams"),
        ({"users": []}, "no users"),
        ({"streams": True}, "streams not a list"),
        ({"streams.0.subjects": True}, "subjects not a string"),
        ({"streams.0.subjects": ["nodalarc.ome.>"]}, "subjects a list"),
        ({"streams.0.subjects": "nodalarc.ome.visibility"}, "subjects not a root wildcard"),
        ({"streams.0.name": "ome"}, "stream name not NODALARC_*"),
        ({"users.0.key": "no such"}, "user key not a values key"),
        ({"users.0.publish": "nodalarc.>"}, "publish not a list"),
        ({"users.0.publish": [1]}, "pattern not a string"),
        ({"users.0.publish": ["nodalarc..ops"]}, "empty token"),
        ({"users.0.publish": ["nodalarc.>.ops"]}, "misplaced wildcard"),
    ],
)
def test_producer_validation_refuses_what_the_chart_would_refuse(changes, label):
    with pytest.raises(nc.MessagingInventoryError):
        nc.validate_messaging_inventory(_inventory_with(**changes))
    assert label


_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _go_unescape(literal: str) -> str:
    """Undo the two escapes a Go interpreted string needs for these regexes: ``\\\\`` and ``\\"``."""
    out: list[str] = []
    index = 0
    while index < len(literal):
        char = literal[index]
        if char == "\\":
            escaped = literal[index + 1]
            assert escaped in ("\\", '"'), literal
            out.append(escaped)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _regex_match_literals(template_text: str) -> list[str]:
    import re

    return [
        _go_unescape(match.group(1))
        for line in template_text.splitlines()
        for match in re.finditer(r'regexMatch "((?:[^"\\]|\\.)*)"', line)
    ]


def test_chart_regexes_equal_the_producer_regexes():
    """The chart's render-time refusals and the producer's validation apply one rule set,
    written once in Go template text and once in Python on either side of the chart
    boundary. This guard pins that deliberate duplication: four sites, four patterns."""
    literals = _regex_match_literals((_ROOT / "deploy/helm/templates/_nats.yaml").read_text())

    assert literals == [
        nc._STREAM_NAME_RE.pattern,
        nc._ROOT_WILDCARD_RE.pattern,
        nc._USER_KEY_RE.pattern,
        nc._SUBJECT_PATTERN_RE.pattern,
    ]


def _values():
    import yaml

    return yaml.safe_load((_ROOT / "deploy/helm/values.yaml").read_text())


def test_checked_in_retention_passes_the_initializer_field_rules():
    """The template refuses a retention entry at render time; the checked-in table must pass
    the template's own rules, read from the template so no third copy of them exists."""
    import re

    template = (_ROOT / "deploy/helm/templates/ome-deployment.yaml").read_text()
    (age_line,) = [line for line in template.splitlines() if "$entry.maxAge | toString" in line]
    (age_pattern,) = _regex_match_literals(age_line)
    (fields_line,) = [line for line in template.splitlines() if "range $field := list" in line]
    integer_fields = re.findall(r'"([A-Za-z]+)"', fields_line.split("list", 1)[1])

    assert integer_fields == ["maxMsgsPerSubject", "maxBytes"]
    for name, entry in _values()["nats"]["streamRetention"].items():
        assert re.fullmatch(age_pattern, entry["maxAge"]), (name, entry["maxAge"])
        for field in integer_fields:
            value = entry[field]
            assert isinstance(value, int) and not isinstance(value, bool), (name, field, value)


def test_values_auth_users_equal_the_inventory_users():
    """The shipped credentials correspond exactly to the registry's user table."""
    assert set(_values()["nats"]["auth"]["users"]) == {user.key for user in nc.NATS_USERS}
