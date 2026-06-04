# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for the e2e matrix acceptance helpers.

These tests keep the cluster-run script honest in the normal unit suite: ground
sessions cannot pass by skipping ground connectivity, and the MBB packet lane
hard-gates emulator-side overlap while recording packet loss as routing behavior.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.integration import e2e_matrix


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


def test_quality_workflow_runs_non_cluster_tests_as_separate_signal() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/quality.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    jobs = workflow["jobs"]

    assert "pull_request" in triggers
    assert jobs["lint"]["runs-on"] == "ubuntu-latest"
    assert jobs["test"]["runs-on"] == "ubuntu-latest"

    lint_steps = jobs["lint"]["steps"]
    test_steps = jobs["test"]["steps"]
    assert any(step.get("run") == "make lint" for step in lint_steps)
    assert not any(step.get("run") == "make test" for step in lint_steps)
    assert any(step.get("uses") == "actions/setup-node@v4" for step in test_steps)
    assert any(step.get("run") == "make test" for step in test_steps)


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
