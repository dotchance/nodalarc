# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for the e2e matrix acceptance helpers.

These tests keep the cluster-run script honest in the normal unit suite: continuous
sessions require routed proof, intermittent sessions preserve valid unreachable
outcomes, and the MBB lane records packet loss as routing behavior.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.segment_session import SegmentSessionConfig
from nodalarc.resolve_session import resolve_session

from tests.integration import e2e_matrix


def test_catalog_deploy_uses_guarded_shipped_revision_and_exact_yaml(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []
    session_ref = "nodalarc:sessions/earth-leo-simple.yaml"
    session_yaml = "session:\n  name: earth-leo-simple\n"

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/v1/sessions":
            return [
                {
                    "source_id": {"kind": "catalog", "session_ref": session_ref},
                    "deploy_allowed": True,
                    "source_revision": "a" * 64,
                    "document_digest": "b" * 64,
                    "dependency_digest": "c" * 64,
                }
            ]
        return {
            "status": "accepted",
            "operation_id": "0123456789abcdef",
            "source": {"kind": "catalog", "session_ref": session_ref},
        }

    monkeypatch.setattr(e2e_matrix, "request_json", fake_request)
    monkeypatch.setattr(
        e2e_matrix.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            text=session_yaml,
            raise_for_status=lambda: None,
        ),
    )

    result = e2e_matrix.deploy_catalog_session(
        "token",
        {"id": "earth-leo-simple", "session_yaml": session_yaml},
    )

    assert result["status"] == "accepted"
    method, path, kwargs = calls[-1]
    assert (method, path) == ("POST", "/api/v1/sessions/switch")
    assert kwargs["json"] == {
        "source": {"kind": "catalog", "session_ref": session_ref},
        "expected_source_revision": "a" * 64,
        "expected_document_digest": "b" * 64,
        "expected_dependency_digest": "c" * 64,
    }


def test_wait_for_transition_is_bound_to_operation_terminal_state(monkeypatch) -> None:
    states = iter(
        [
            {"state": "switching"},
            {"state": "succeeded", "runtime": {"session_id": "resolved", "generation": 2}},
        ]
    )
    paths: list[str] = []

    def fake_request(_method: str, path: str, **_kwargs):
        paths.append(path)
        return next(states)

    monkeypatch.setattr(e2e_matrix, "request_json", fake_request)
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _seconds: None)

    result = e2e_matrix.wait_for_transition("token", "0123456789abcdef", timeout=5)

    assert result["state"] == "succeeded"
    assert paths == [
        "/api/v1/session-transitions/0123456789abcdef",
        "/api/v1/session-transitions/0123456789abcdef",
    ]


def test_mbb_acceptance_mutation_stays_in_canonical_session_grammar() -> None:
    rendered = e2e_matrix._acceptance_session_yaml(  # noqa: SLF001
        session_name="mbb-mutated",
        mbb_overlap_ticks=17,
    )
    document = load_configuration_yaml(rendered)

    parsed = SegmentSessionConfig.model_validate(document)
    resolved = resolve_session(document)
    ground = next(segment for segment in document["segments"] if segment["id"] == "ground")

    assert parsed.session.name == "mbb-mutated"
    assert ground["apply"]["scheduling"]["mbb_overlap_ticks"] == 17
    assert document["segments"][0]["source"].startswith("nodalarc:")
    assert resolved.nodes
    assert resolved.link_candidates


def test_mbb_packet_acceptance_requires_successor_fib_overlap() -> None:
    output = {"protocol_observed": True, "packet_outcome": "loss_observed", "zero_loss": False}

    assert e2e_matrix._mbb_packet_window_passed(  # noqa: SLF001
        output, {"successor_fib_ready": True}, []
    )
    assert not e2e_matrix._mbb_packet_window_passed(  # noqa: SLF001
        output, {"successor_fib_ready": False}, []
    )
    assert not e2e_matrix._mbb_packet_window_passed(output, None, [])  # noqa: SLF001
    assert not e2e_matrix._mbb_packet_window_passed(  # noqa: SLF001
        output, {"successor_fib_ready": True}, [{"code": "KERNEL_DIRTY"}]
    )


def test_check_ping_fails_ground_session_when_ground_probe_is_not_proven(monkeypatch) -> None:
    state = {
        "nodes": [
            {"node_id": "gs-denver", "node_type": "ground_station"},
            {"node_id": "sat-P00S00", "node_type": "satellite"},
        ],
        "links": [],
    }
    monkeypatch.setattr(e2e_matrix, "request_json", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        e2e_matrix,
        "_find_routed_ground_probe",
        lambda *args, **kwargs: {"result": "FAIL", "reason": "no GS route"},
    )

    result = e2e_matrix.check_ping("token", {"protocol": "isis"})

    assert result["result"] == "FAIL"
    assert result["mode"] == "ground_to_ground"
    assert result["ground_node_count"] == 1


def test_check_ping_fails_when_declared_ground_nodes_do_not_materialize(monkeypatch) -> None:
    state = {
        "nodes": [
            {"node_id": "sat-P00S00", "node_type": "satellite"},
            {"node_id": "sat-P00S01", "node_type": "satellite"},
        ],
        "links": [],
    }
    monkeypatch.setattr(e2e_matrix, "request_json", lambda *args, **kwargs: state)

    result = e2e_matrix.check_ping(
        "token", {"protocol": "isis", "gs": "configs/ground-stations/sets/global.yaml"}
    )

    assert result["result"] == "FAIL"
    assert result["mode"] == "ground_to_ground"
    assert result["ground_declared"] is True
    assert result["ground_node_count"] == 0


def test_check_ping_allows_skip_only_for_satellite_only_topology(monkeypatch) -> None:
    state = {
        "nodes": [
            {"node_id": "sat-P00S00", "node_type": "satellite"},
            {"node_id": "sat-P00S01", "node_type": "satellite"},
        ],
        "links": [],
    }
    monkeypatch.setattr(e2e_matrix, "request_json", lambda *args, **kwargs: state)

    result = e2e_matrix.check_ping("token", {"protocol": "isis"})

    assert result["result"] == "SKIP"
    assert result["active_link_count"] == 0


def test_connectivity_expectation_marks_only_polar_session_intermittent() -> None:
    polar = e2e_matrix._connectivity_expectation("earth-leo-polar")  # noqa: SLF001
    ordinary = e2e_matrix._connectivity_expectation("earth-leo-simple")  # noqa: SLF001

    assert polar == {
        "mode": "intermittent",
        "disconnected_offset_seconds": 120,
        "settle_seconds": 30,
    }
    assert ordinary == {"mode": "continuous"}


def test_intermittent_connectivity_accepts_proven_runtime_unreachability(monkeypatch) -> None:
    controls: list[str] = []
    waits: list[int | None] = []

    def fake_seek(_token: str, target_sim_time: str) -> dict:
        controls.append(target_sim_time)
        return {"result": "PASS"}

    def fake_ping(_token: str, _perm: dict, *, ground_wait_s: int | None = None) -> dict:
        waits.append(ground_wait_s)
        return {"result": "FAIL", "reason": "no route", "active_link_count": 34}

    monkeypatch.setattr(e2e_matrix, "_seek_playback_and_pause", fake_seek)
    monkeypatch.setattr(e2e_matrix, "check_ping", fake_ping)
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        e2e_matrix,
        "request_json",
        lambda *args, **kwargs: {"state": "playing", "paused": False},
    )

    result = e2e_matrix.check_intermittent_connectivity(
        "token",
        {
            "session_start_time": "2026-06-08T00:00:00Z",
            "connectivity_expectation": e2e_matrix._connectivity_expectation(  # noqa: SLF001
                "earth-leo-polar"
            ),
        },
    )

    assert result["result"] == "PASS"
    assert result["disconnected_probe"]["result"] == "FAIL"
    assert result["observed_outcome"] == "unreachable"
    assert controls == ["2026-06-08T00:02:00+00:00"]
    assert waits == [15]


def test_ground_probe_reaches_candidate_after_first_bounded_sweep(monkeypatch) -> None:
    source = "ground-source"
    destinations = [f"ground-destination-{index:02d}" for index in range(1, 18)]
    ground_topology = {
        source: {
            "site": "source-site",
            "body": "earth",
            "lat_deg": 0.0,
            "lon_deg": 0.0,
            "wan_ifnames": ["term0"],
        },
        **{
            destination: {
                "site": f"destination-site-{index:02d}",
                "body": "earth",
                "lat_deg": float(index),
                "lon_deg": float(index),
                "wan_ifnames": ["term0"],
            }
            for index, destination in enumerate(destinations, start=1)
        },
    }
    candidates = [(source, destination) for destination in destinations]
    active_ground_links = {node_id: [{"state": "active"}] for node_id in ground_topology}
    command_log: list[str] = []

    monkeypatch.setattr(e2e_matrix, "request_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(e2e_matrix, "_ground_node_ids", lambda state: destinations)
    monkeypatch.setattr(e2e_matrix, "_ground_links_by_gs", lambda state: active_ground_links)
    monkeypatch.setattr(e2e_matrix, "_transit_pairs", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        e2e_matrix,
        "_node_loopback_ip",
        lambda node_id: f"10.0.0.{destinations.index(node_id) + 1}",
    )

    def fake_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
        del node_id, timeout
        command_log.append(command)
        if command.startswith("ip route get"):
            destination_ip = command.rsplit(" ", 1)[-1]
            if destination_ip == "10.0.0.17":
                return {
                    "rc": 0,
                    "stdout": f"{destination_ip} via 100.64.0.1 dev term0",
                    "stderr": "",
                }
            return {"rc": 2, "stdout": "", "stderr": "unreachable"}
        if command.startswith("vtysh"):
            return {"rc": 0, "stdout": "neighbor Up", "stderr": ""}
        if command.startswith("ping"):
            return {"rc": 0, "stdout": "0% packet loss", "stderr": ""}
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(e2e_matrix, "_kubectl_exec", fake_exec)

    result = e2e_matrix._find_routed_ground_probe(  # noqa: SLF001
        "token",
        wait_s=1,
        ground_topology=ground_topology,
    )

    assert result is not None
    assert result["result"] == "PASS"
    assert result["key"] == f"{source}->{destinations[16]}"
    assert len([command for command in command_log if command.startswith("ip route get")]) == 17
    assert len([command for command in command_log if command.startswith("vtysh")]) == 1
    assert len([command for command in command_log if command.startswith("ping")]) == 1


def test_quality_workflow_runs_lint_and_frontend_smoke_as_separate_signals() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/quality.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    jobs = workflow["jobs"]

    assert "pull_request" in triggers
    assert jobs["lint"]["runs-on"] == "ubuntu-latest"
    assert jobs["frontend"]["runs-on"] == "ubuntu-latest"

    lint_steps = jobs["lint"]["steps"]
    frontend_steps = jobs["frontend"]["steps"]
    assert any(step.get("run") == "make lint" for step in lint_steps)
    assert not any(step.get("run") == "make test" for step in lint_steps)
    assert any(step.get("uses") == "actions/setup-node@v4" for step in frontend_steps)
    assert any(step.get("run") == "make test-frontend" for step in frontend_steps)
    assert any(step.get("run") == "make build-frontends" for step in frontend_steps)
    assert not any(step.get("run") == "make test" for step in frontend_steps)


def test_matrix_result_classification_distinguishes_expected_failures() -> None:
    xpass = {"result": "PASS"}
    assert (
        e2e_matrix._classify_matrix_result(  # noqa: SLF001
            xpass, {"xfail": "known limitation"}
        )
        == "xpass"
    )
    assert xpass["result"] == "XPASS"
    assert xpass["xfail_reason"] == "known limitation"

    xfail = {"result": "FAIL"}
    assert (
        e2e_matrix._classify_matrix_result(  # noqa: SLF001
            xfail, {"xfail": "known limitation"}
        )
        == "xfail"
    )
    assert xfail["result"] == "XFAIL"

    normal_pass = {"result": "PASS"}
    assert e2e_matrix._classify_matrix_result(normal_pass, {}) == "pass"  # noqa: SLF001

    normal_fail = {"result": "FAIL"}
    assert e2e_matrix._classify_matrix_result(normal_fail, {}) == "fail"  # noqa: SLF001
