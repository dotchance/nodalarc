# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for the e2e matrix acceptance helpers.

These tests exercise the cluster-run script in the normal unit suite: continuous
sessions require routed proof, intermittent sessions preserve valid unreachable
outcomes, and the MBB lane records packet loss as routing behavior.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]


_ISIS_TABLE_UP = (
    "Area NODAL:\n System Id           Interface   L  State         Holdtime SNPA\n"
    " leo-sat-p00s13      term0       3  Up            2        2020.2020.2020\n"
)
_ISIS_TABLE_DOWN = (
    "Area NODAL:\n System Id           Interface   L  State         Holdtime SNPA\n"
    " leo-sat-p00s13      term0       3  Initializing  2        2020.2020.2020\n"
)
_PING_ZERO_LOSS = (
    "PING 100.64.0.17: 56 data bytes\n3 packets transmitted, 3 packets received, 0% packet loss\n"
)
_PING_TOTAL_LOSS = (
    "PING 100.64.0.17: 56 data bytes\n3 packets transmitted, 0 packets received, 100% packet loss\n"
)
_PING_PARTIAL_LOSS = "10 packets transmitted, 9 received, 10% packet loss, time 9012ms\n"
from tests.integration import e2e_matrix


def test_runtime_evidence_provenance_is_complete_and_identity_bound(monkeypatch) -> None:
    values = {
        "NODALARC_EVIDENCE_SOURCE_GIT_SHA": "0123456789abcdef",
        "NODALARC_EVIDENCE_SOURCE_TAG": "01234567-dirty.deadbeef",
        "NAMESPACE": "nodalarc-test",
        "NODALARC_EXPECTED_RUNTIME_RELEASE": "0.5.2",
        "NODALARC_EXPECTED_RUNTIME_BUILD": "01234567-dirty.deadbeef",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    provenance = e2e_matrix._run_provenance_from_environment()  # noqa: SLF001

    assert provenance == {
        "source_git_sha": "0123456789abcdef",
        "source_tree_tag": "01234567-dirty.deadbeef",
        "namespace": "nodalarc-test",
        "expected_runtime_release": "0.5.2",
        "expected_runtime_build": "01234567-dirty.deadbeef",
    }
    assert (
        e2e_matrix._runtime_identity_error(  # noqa: SLF001
            provenance,
            {"release": "0.5.2", "build": "01234567-dirty.deadbeef"},
        )
        is None
    )
    assert "differs from the checkout" in e2e_matrix._runtime_identity_error(  # noqa: SLF001
        provenance,
        {"release": "0.5.2", "build": "stale"},
    )


def test_matrix_evidence_preserves_other_run_directories() -> None:
    source = Path(e2e_matrix.__file__).read_text()

    assert "rmtree(evidence_root)" not in source
    assert '/ "runtime-matrix"' in source
    assert "NODALARC_E2E_EVIDENCE_DIR" in source


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
        return {
            "result": "FAIL",
            "failure_kind": "connectivity",
            "reason": "kernel answered Network is unreachable",
            "active_link_count": 34,
        }

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
        "_published_loopback_ip",
        lambda node_id, nodes_by_id: f"100.64.0.{destinations.index(node_id) + 1}",
    )

    def fake_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
        del node_id, timeout
        command_log.append(command)
        if command.startswith("ip route get"):
            destination_ip = command.rsplit(" ", 1)[-1]
            if destination_ip == "100.64.0.17":
                return {
                    "rc": 0,
                    "stdout": f"{destination_ip} via 100.64.0.1 dev term0",
                    "stderr": "",
                }
            return {"rc": 2, "stdout": "", "stderr": "RTNETLINK answers: Network is unreachable"}
        if command.startswith("vtysh"):
            return {"rc": 0, "stdout": _ISIS_TABLE_UP, "stderr": ""}
        if command.startswith("ping"):
            return {"rc": 0, "stdout": _PING_ZERO_LOSS, "stderr": ""}
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


# --- workload targeting and observation contracts (the matrix consumes the published contracts) ---


def _pod_document(
    node_id: str,
    *,
    uid: str = "uid-1",
    primary: str | None = "frr-router",
    containers=("frr-router", "observer"),
    deleting: bool = False,
) -> dict:
    metadata = {
        "name": node_id,
        "namespace": "nodalarc",
        "uid": uid,
        "labels": {"nodalarc.io/node-id": node_id},
        "annotations": {},
    }
    if primary is not None:
        metadata["annotations"]["nodalarc.io/primary-container"] = primary
    if deleting:
        metadata["deletionTimestamp"] = "2026-09-09T00:00:00Z"
    return {"metadata": metadata, "spec": {"containers": [{"name": c} for c in containers]}}


class _FakeRun:
    """A subprocess.run stand-in that answers the harness's kubectl commands."""

    def __init__(
        self, pods: list[dict], exec_answers: dict[str, tuple[int, str, str]] | None = None
    ):
        self.pods = pods
        self.exec_answers = exec_answers or {}
        self.commands: list[str] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if " get pods " in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"items": self.pods}), stderr="")
        for needle, (rc, out, err) in self.exec_answers.items():
            if needle in command:
                return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        return SimpleNamespace(returncode=1, stdout="", stderr=f"unexpected command {command}")


def test_workload_target_resolves_the_published_primary_container(monkeypatch) -> None:
    fake = _FakeRun([_pod_document("gs-den")])
    monkeypatch.setattr(subprocess, "run", fake)

    target, error = e2e_matrix._workload_target("gs-den")  # noqa: SLF001

    assert error is None
    assert (target.pod_name, target.container, target.namespace) == (
        "gs-den",
        "frr-router",
        "nodalarc",
    )
    assert "-l nodalarc.io/node-id=gs-den" in fake.commands[0]


def test_workload_target_refuses_pods_that_do_not_establish_the_node(monkeypatch) -> None:
    cases = {
        "labelled for another node": [
            {
                **_pod_document("gs-other"),
                "metadata": {
                    **_pod_document("gs-other")["metadata"],
                    "labels": {"nodalarc.io/node-id": "gs-other"},
                },
            }
        ],
        "two live pods": [_pod_document("gs-den", uid="a"), _pod_document("gs-den", uid="b")],
        "no annotation": [_pod_document("gs-den", primary=None)],
        "annotation names an undeclared container": [_pod_document("gs-den", primary="frr")],
        "no pod": [],
        "only a deleting pod": [_pod_document("gs-den", deleting=True)],
    }
    for label, pods in cases.items():
        monkeypatch.setattr(subprocess, "run", _FakeRun(pods))
        target, error = e2e_matrix._workload_target("gs-den")  # noqa: SLF001
        assert target is None, label
        assert error, label


def test_workload_exec_runs_nothing_without_a_resolved_target(monkeypatch) -> None:
    fake = _FakeRun([])
    monkeypatch.setattr(subprocess, "run", fake)

    result = e2e_matrix._workload_exec("gs-den", "ip route get 100.64.0.1")  # noqa: SLF001

    assert result["resolution_error"]
    assert result["rc"] is None
    assert not any(" exec " in command for command in fake.commands)


def test_workload_exec_targets_the_published_container_by_pod_name(monkeypatch) -> None:
    fake = _FakeRun([_pod_document("gs-den")], {"ip route get": (0, "100.64.0.1 dev term0", "")})
    monkeypatch.setattr(subprocess, "run", fake)

    result = e2e_matrix._workload_exec("gs-den", "ip route get 100.64.0.1")  # noqa: SLF001

    assert result["rc"] == 0
    exec_command = next(c for c in fake.commands if " exec " in c)
    assert " exec -n nodalarc gs-den -c frr-router -- ip route get 100.64.0.1" in exec_command


def test_route_observation_accepts_only_recognized_unreachability_as_negative() -> None:
    obs = e2e_matrix._route_observation  # noqa: SLF001
    positive = obs(
        {"rc": 0, "stdout": "100.64.0.1 via 10.1.0.1 dev term0 src 100.64.0.2", "stderr": ""},
        "100.64.0.1",
    )
    assert (positive["observed"], positive["positive"], positive["egress_dev"]) == (
        True,
        True,
        "term0",
    )
    # ENETUNREACH and EHOSTUNREACH in glibc's and musl's wording; the Alpine
    # router image answers "Network unreachable", as the polar window showed.
    for answer in (
        "Network is unreachable",
        "Network unreachable",
        "No route to host",
        "Host is unreachable",
    ):
        negative = obs(
            {
                "rc": 2,
                "stdout": "",
                "stderr": f"RTNETLINK answers: {answer}\ncommand terminated with exit code 2",
            },
            "100.64.0.1",
        )
        assert (negative["observed"], negative["positive"]) == (True, False), answer
    for failure in (
        {"rc": 2, "stdout": "", "stderr": "RTNETLINK answers: Operation not permitted"},
        {"rc": 2, "stdout": "", "stderr": "RTNETLINK answers: Invalid argument"},
        {
            "rc": 126,
            "stdout": "",
            "stderr": 'OCI runtime exec failed: exec failed: unable to start container process: exec: "ip": executable file not found in $PATH',
        },
        {
            "rc": 1,
            "stdout": "",
            "stderr": "Error from server (BadRequest): container frr is not valid for pod gs-den",
        },
        {
            "rc": None,
            "stdout": "",
            "stderr": "",
            "resolution_error": "expected one live session pod, found 0",
        },
    ):
        assert obs(failure, "100.64.0.1")["observed"] is False, failure


def test_adjacency_observation_requires_the_daemons_table() -> None:
    obs = e2e_matrix._adjacency_observation  # noqa: SLF001
    up = obs({"rc": 0, "stdout": _ISIS_TABLE_UP, "stderr": ""}, "isis")
    assert (up["observed"], up["positive"]) == (True, True)
    down = obs({"rc": 0, "stdout": _ISIS_TABLE_DOWN, "stderr": ""}, "isis")
    assert (down["observed"], down["positive"]) == (True, False)
    for failure in (
        {"rc": 1, "stdout": "", "stderr": "Exiting: failed to connect to any daemons."},
        {"rc": 0, "stdout": "% Unknown command: show isis neighbor", "stderr": ""},
        {"rc": 126, "stdout": "", "stderr": 'exec: "vtysh": executable file not found in $PATH'},
        {"rc": 0, "stdout": "", "stderr": ""},
    ):
        assert obs(failure, "isis")["observed"] is False, failure


def test_ping_statistics_are_parsed_numerically_not_by_substring() -> None:
    parse = e2e_matrix._parse_ping_statistics  # noqa: SLF001
    assert parse(_PING_ZERO_LOSS)["loss_class"] == "zero_loss"
    assert parse(_PING_TOTAL_LOSS)["loss_class"] == "total_loss"
    assert parse(_PING_PARTIAL_LOSS)["loss_class"] == "partial_loss"
    assert parse("PING x\n64 bytes from 100.64.0.17: seq=0 ttl=64 time=1 ms\n") is None
    # The old substring rule called both of these zero loss.
    assert "0% packet loss" in _PING_TOTAL_LOSS and "0% packet loss" in _PING_PARTIAL_LOSS

    outcome = e2e_matrix._ping_packet_outcome  # noqa: SLF001
    assert outcome(_PING_TOTAL_LOSS, "", 1)["zero_loss"] is False
    assert outcome(_PING_TOTAL_LOSS, "", 1)["loss_class"] == "total_loss"
    assert outcome(_PING_PARTIAL_LOSS, "", 1)["packet_outcome"] == "loss_observed"
    assert outcome(_PING_ZERO_LOSS, "", 0)["zero_loss"] is True


def test_packet_observation_requires_statistics_or_a_kernel_answer() -> None:
    obs = e2e_matrix._packet_observation  # noqa: SLF001
    zero = obs({"rc": 0, "stdout": _PING_ZERO_LOSS, "stderr": ""})
    assert (zero["observed"], zero["positive"], zero["replies"]) == (True, True, True)
    total = obs({"rc": 1, "stdout": _PING_TOTAL_LOSS, "stderr": ""})
    assert (total["observed"], total["positive"], total["replies"]) == (True, False, False)
    partial = obs({"rc": 1, "stdout": _PING_PARTIAL_LOSS, "stderr": ""})
    assert (partial["observed"], partial["positive"], partial["replies"]) == (True, False, True)
    unreachable = obs({"rc": 1, "stdout": "", "stderr": "ping: sendto: Network is unreachable"})
    assert (unreachable["observed"], unreachable["positive"]) == (True, False)
    musl = obs({"rc": 1, "stdout": "", "stderr": "ping: sendto: Network unreachable"})
    assert (musl["observed"], musl["positive"]) == (True, False)
    # The same words inside an exec or runtime diagnostic are not ping's answer.
    for diagnostic in (
        "error: unable to upgrade connection: Network unreachable",
        "Error from server: dial tcp 10.42.0.5:10250: connect: Network is unreachable",
        "OCI runtime exec failed: exec failed: No route to host",
    ):
        assert obs({"rc": 1, "stdout": "", "stderr": diagnostic})["observed"] is False, diagnostic
    sequence_only = obs(
        {"rc": 1, "stdout": "64 bytes from 100.64.0.17: seq=0 ttl=64 time=1 ms\n", "stderr": ""}
    )
    assert sequence_only["observed"] is False
    assert obs({"rc": 127, "stdout": "", "stderr": "sh: ping: not found"})["observed"] is False


def test_sweep_verdict_never_turns_failed_or_absent_probes_into_disconnection() -> None:
    verdict = e2e_matrix._sweep_verdict  # noqa: SLF001
    negative = {
        "candidate": "a->b",
        "kind": "observed_negative",
        "reason": "kernel answered Network is unreachable",
    }
    failure = {
        "candidate": "a->c",
        "kind": "probe_failure",
        "reason": "no published router loopback for c",
    }
    mixed = verdict([negative, failure], "none")
    assert mixed["failure_kind"] == "probe"
    assert "no published router loopback for c" in mixed["reason"]
    assert [a["candidate"] for a in mixed["attempts"]] == ["a->b", "a->c"]
    only_negatives = verdict([negative, {**negative, "candidate": "a->d"}], "none")
    assert only_negatives["failure_kind"] == "connectivity"
    nothing = verdict([], "no transit-capable probe pair")
    assert nothing["failure_kind"] == "probe"
    assert nothing["reason"].startswith("no probe executed")


def test_intermittent_connectivity_refuses_a_probe_that_did_not_complete(monkeypatch) -> None:
    monkeypatch.setattr(e2e_matrix, "_seek_playback_and_pause", lambda _t, _s: {"result": "PASS"})
    monkeypatch.setattr(
        e2e_matrix,
        "check_ping",
        lambda _t, _p, *, ground_wait_s=None: {
            "result": "FAIL",
            "failure_kind": "probe",
            "reason": "probe failed: no published router loopback for gs-x",
            "active_link_count": 35,
        },
    )
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        e2e_matrix, "request_json", lambda *a, **k: {"state": "playing", "paused": False}
    )

    result = e2e_matrix.check_intermittent_connectivity(
        "token",
        {
            "session_start_time": "2026-06-08T00:00:00Z",
            "connectivity_expectation": e2e_matrix._connectivity_expectation("earth-leo-polar"),  # noqa: SLF001
        },
    )

    assert result["result"] == "FAIL"
    assert "did not complete an observation" in result["reason"]
    assert "observed_outcome" not in result


def test_harness_targets_no_container_by_literal_name_or_derived_pod_name() -> None:
    source = (Path(e2e_matrix.__file__)).read_text()
    assert "-c frr" not in source
    assert ".lower()" not in source
    assert '"0% packet loss" in' not in source


def test_ping_statistics_must_be_consistent_numbers_from_a_transmission() -> None:
    parse = e2e_matrix._parse_ping_statistics  # noqa: SLF001
    obs = e2e_matrix._packet_observation  # noqa: SLF001
    outcome = e2e_matrix._ping_packet_outcome  # noqa: SLF001
    for line, problem in (
        ("0 packets transmitted, 0 packets received, 0% packet loss", "no packet was transmitted"),
        ("5 packets transmitted, 7 received, 0% packet loss, time 4ms", "exceeds"),
        ("5 packets transmitted, 5 received, 40% packet loss, time 4ms", "contradicts"),
        ("10 packets transmitted, 0 received, 0% packet loss, time 9ms", "contradicts"),
    ):
        parsed = parse(line)
        assert (
            parsed is not None and parsed["consistent"] is False and problem in parsed["problem"]
        ), line
        assert obs({"rc": 0, "stdout": line, "stderr": ""})["observed"] is False, line
        window = outcome(line, "", 0)
        assert window["packet_outcome"] == "probe_error" and window["protocol_observed"] is False, (
            line
        )
    consistent = parse("10 packets transmitted, 9 received, 10% packet loss, time 9012ms")
    assert consistent["consistent"] is True and consistent["loss_class"] == "partial_loss"
    rounded = parse("3 packets transmitted, 2 packets received, 33% packet loss")
    assert rounded["consistent"] is True and rounded["loss_class"] == "partial_loss"


def test_sweep_verdict_retains_every_attempt_and_every_reason() -> None:
    verdict = e2e_matrix._sweep_verdict  # noqa: SLF001
    attempts = [
        {"candidate": f"a->{i}", "kind": "probe_failure", "reason": f"reason {i % 7}"}
        for i in range(60)
    ] + [
        {
            "candidate": f"b->{i}",
            "kind": "observed_negative",
            "reason": f"b->{i}: no route (kernel answered Network unreachable)",
        }
        for i in range(30)
    ]
    result = verdict(attempts, "none")
    assert result["failure_kind"] == "probe"
    assert len(result["attempts"]) == 90
    assert result["attempt_count"] == 90
    assert result["probe_failure_count"] == 60
    assert result["observed_negative_count"] == 30
    assert result["probe_failure_reasons"] == [f"reason {i}" for i in range(7)]
    assert "and 4 more distinct reasons" in result["reason"]
    negatives = verdict(attempts[60:], "none")
    assert negatives["failure_kind"] == "connectivity"
    assert len(negatives["attempts"]) == 30 and negatives["attempt_count"] == 30
    assert "30 attempts, all observed negative" in negatives["reason"]


def test_ground_probe_records_the_kernel_answer_and_runs_nothing_without_a_published_loopback(
    monkeypatch,
) -> None:
    topology = {
        "gs-a": {
            "site": "a",
            "body": "earth",
            "lat_deg": 0.0,
            "lon_deg": 0.0,
            "wan_ifnames": ["term0"],
        },
        "gs-b": {
            "site": "b",
            "body": "earth",
            "lat_deg": 10.0,
            "lon_deg": 10.0,
            "wan_ifnames": ["term0"],
        },
        "gs-c": {
            "site": "c",
            "body": "earth",
            "lat_deg": 20.0,
            "lon_deg": 20.0,
            "wan_ifnames": ["term0"],
        },
    }
    state = {
        "nodes": [
            {
                "node_id": "gs-a",
                "node_type": "ground_station",
                "addresses": [
                    {"purpose": "router_loopback", "family": "ipv4", "address": "100.64.0.1/32"}
                ],
            },
            {
                "node_id": "gs-b",
                "node_type": "ground_station",
                "addresses": [
                    {"purpose": "router_loopback", "family": "ipv4", "address": "100.64.0.2/32"}
                ],
            },
            {"node_id": "gs-c", "node_type": "ground_station", "addresses": []},
        ],
        "links": [],
    }
    executed: list[tuple[str, str]] = []

    def fake_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
        executed.append((node_id, command))
        return {
            "rc": 2,
            "stdout": "",
            "stderr": "RTNETLINK answers: Network unreachable\ncommand terminated with exit code 2",
        }

    monkeypatch.setattr(e2e_matrix, "request_json", lambda *a, **k: state)
    active = [{"state": "active"}]
    monkeypatch.setattr(
        e2e_matrix,
        "_ground_links_by_gs",
        lambda s: {"gs-a": active, "gs-b": active, "gs-c": active},
    )
    monkeypatch.setattr(
        e2e_matrix, "_transit_pairs", lambda *a, **k: [("gs-a", "gs-b"), ("gs-a", "gs-c")]
    )
    monkeypatch.setattr(e2e_matrix, "_kubectl_exec", fake_exec)
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _s: None)

    result = e2e_matrix._find_routed_ground_probe("token", wait_s=1, ground_topology=topology)  # noqa: SLF001

    assert result["result"] == "FAIL"
    assert result["failure_kind"] == "probe"
    by_candidate = {a["candidate"]: a for a in result["attempts"]}
    assert by_candidate["gs-a->gs-c"]["kind"] == "probe_failure"
    assert "no published router loopback for gs-c" in by_candidate["gs-a->gs-c"]["reason"]
    assert all(node != "gs-c" and "100.64.0.3" not in command for node, command in executed)
    negative = by_candidate["gs-a->gs-b"]
    assert negative["kind"] == "observed_negative"
    assert "kernel answered Network unreachable" in negative["reason"]
    assert negative["observation"]["route"] == "kernel answered Network unreachable"
    assert "RTNETLINK answers: Network unreachable" in negative["route_stderr"]


class _FakeProc:
    def __init__(self, stdout: str) -> None:
        self._stdout = stdout
        self.pid = 4242
        self.returncode = 0
        self.communicated = False

    def poll(self):
        return 0

    def communicate(self, timeout=None):
        self.communicated = True
        return self._stdout, ""

    def kill(self):
        raise AssertionError("kill must not be needed for a finished probe")


def test_handover_packet_window_targets_the_published_container(monkeypatch) -> None:
    started: list[dict] = []

    def fake_popen(cmd, **kwargs):
        started.append({"cmd": cmd, **kwargs})
        return _FakeProc(
            "64 bytes from 100.64.0.2: seq=0 ttl=64 time=1.0 ms\n64 bytes from 100.64.0.2: seq=1 ttl=64 time=1.0 ms\n2 packets transmitted, 2 packets received, 0% packet loss\n"
        )

    probe = {"key": "gs-a->gs-b", "src": "gs-a", "dst_gs": "gs-b", "dst_ip": "100.64.0.2"}
    target = e2e_matrix.WorkloadTarget(
        node_id="gs-a", namespace="nodalarc", pod_name="gs-a", pod_uid="u", container="frr-router"
    )
    monkeypatch.setattr(e2e_matrix, "_find_all_routed_ground_probes", lambda token: [probe])
    monkeypatch.setattr(e2e_matrix, "_workload_target", lambda node_id: (target, None))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        e2e_matrix,
        "request_json",
        lambda method, path, **k: [] if "ops/events" in path else {"nodes": [], "links": []},
    )
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _s: None)

    result = e2e_matrix._run_mbb_packet_window("token", count=2, interval_s=0.2)  # noqa: SLF001

    assert len(started) == 1
    assert (
        " exec -n nodalarc gs-a -c frr-router -- ping -c 2 -i 0.2 -W 1 100.64.0.2"
        in started[0]["cmd"]
    )
    assert started[0]["start_new_session"] is True
    assert "-c frr " not in started[0]["cmd"] and "gs-a.lower" not in started[0]["cmd"]
    output = result["probe_outputs"]["gs-a->gs-b"]
    assert output["packet_outcome"] == "zero_loss" and output["reply_count"] == 2


def test_handover_packet_window_starts_nothing_when_a_target_is_unresolved(monkeypatch) -> None:
    probe = {"key": "gs-a->gs-b", "src": "gs-a", "dst_gs": "gs-b", "dst_ip": "100.64.0.2"}
    monkeypatch.setattr(e2e_matrix, "_find_all_routed_ground_probes", lambda token: [probe])
    monkeypatch.setattr(
        e2e_matrix,
        "_workload_target",
        lambda node_id: (None, "expected one live session pod, found 0: []"),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen must not start")),
    )

    result = e2e_matrix._run_mbb_packet_window("token", count=2, interval_s=0.2)  # noqa: SLF001

    assert result["result"] == "FAIL"
    assert result["failure_kind"] == "probe"
    assert "expected one live session pod" in result["reason"]


def test_handover_window_decides_pings_no_route_answer_with_the_one_reader() -> None:
    """The interrupted-window parser and the synchronous probes share one decision
    about a no-route answer: only a line ping printed, in either C library's wording."""
    outcome = e2e_matrix._ping_packet_outcome  # noqa: SLF001
    for answer in ("Network is unreachable", "Network unreachable", "No route to host"):
        observed = outcome("", f"ping: sendto: {answer}", 1)
        assert observed["packet_outcome"] == "routing_unreachable", answer
        assert observed["protocol_observed"] is True
    for diagnostic in (
        "error: unable to upgrade connection: Network unreachable",
        "Error from server: dial tcp 10.42.0.5:10250: connect: Network is unreachable",
    ):
        unobserved = outcome("", diagnostic, 1)
        assert unobserved["protocol_observed"] is False, diagnostic
        assert unobserved["packet_outcome"] == "probe_error", diagnostic
    source = Path(e2e_matrix.__file__).read_text()
    assert source.count('"Network unreachable" in') == 0


def test_handover_overlap_reads_adjacency_and_route_through_the_observation_parsers(
    monkeypatch,
) -> None:
    """The MBB overlap sample uses the same readers as every other probe: no inline
    'Up' substring, no second route parser."""
    source = Path(e2e_matrix.__file__).read_text()
    window = source.split("def _run_mbb_packet_window")[1].split("\ndef ")[0]
    assert '"Up" in' not in window
    assert "_adjacency_observation(" in window and "_route_observation(" in window
    assert source.count("def _route_egress_dev") == 1 and "def _route_dev" not in source


# --- the MBB acceptance lanes run the shipped walker unchanged ---


_PROVENANCE = {
    "source_git_sha": "abc",
    "source_tree_tag": "abc-1",
    "namespace": "nodalarc",
    "expected_runtime_release": "0.7.1+test",
    "expected_runtime_build": "abc-1",
}


def test_acceptance_permutation_is_the_unchanged_shipped_walker_with_resolved_station_facts() -> (
    None
):
    import hashlib

    perm = e2e_matrix.acceptance_permutation(_PROVENANCE)
    walker = ROOT / "catalog/nodalarc/sessions/earth-leo-walker.yaml"

    assert perm["id"] == "earth-leo-walker"
    assert perm["session_ref"] == "nodalarc:sessions/earth-leo-walker.yaml"
    assert perm["document_sha256"] == hashlib.sha256(walker.read_bytes()).hexdigest()
    assert perm["session_yaml"] == walker.read_text()
    assert perm["run_provenance"] == _PROVENANCE
    assert perm["protocol"] == "isis" and perm["step_seconds"] == 1
    stations = perm["mbb_stations"]
    assert {s["handover_mode"] for s in stations.values()} == {"mbb"}
    assert {s["mbb_overlap_ticks"] for s in stations.values()} == {30}
    assert {s["mbb_reserve"] for s in stations.values()} == {1}
    assert {node: s["steady_limit"] for node, s in stations.items()} == {
        "earth-us-hawthorne-gw1": 7,
        "earth-us-co-denver-gw1": 3,
        "earth-us-co-denver-gw2": 1,
        "earth-us-va-ashburn-gw1": 1,
        "earth-de-frankfurt-gw1": 1,
    }
    assert set(perm["ground_topology"]) == set(stations)
    assert perm["ground_topology"]["earth-us-va-ashburn-gw1"]["wan_ifnames"] == ["term0", "term1"]
    assert perm["ground_topology"]["earth-us-co-denver-gw2"]["site"] == "earth-us-co-denver"


def test_shipped_deploy_checks_the_transition_identity_and_the_document_digest(monkeypatch) -> None:
    perm = {
        "id": "earth-leo-walker",
        "document_sha256": "d" * 64,
        "run_provenance": _PROVENANCE,
    }
    facts = {"release": "0.7.1+test", "build": "abc-1", "document_digest": "sha256:" + "d" * 64}
    waited: list[str] = []
    monkeypatch.setattr(
        e2e_matrix,
        "deploy_catalog_session",
        lambda token, perm: {"status": "accepted", "operation_id": "op-1"},
    )
    monkeypatch.setattr(
        e2e_matrix,
        "wait_for_transition",
        lambda token, operation_id, timeout=600: (
            waited.append(operation_id),
            {"state": "succeeded", "facts": facts},
        )[1],
    )
    passed = e2e_matrix.deploy_shipped_and_wait("t", perm)
    assert passed["result"] == "PASS"
    assert passed["observed_runtime"]["document_digest"] == "sha256:" + "d" * 64
    assert waited == ["op-1"]

    other_build = {**facts, "build": "zzz-9"}
    monkeypatch.setattr(
        e2e_matrix,
        "wait_for_transition",
        lambda token, operation_id, timeout=600: {"state": "succeeded", "facts": other_build},
    )
    mismatched = e2e_matrix.deploy_shipped_and_wait("t", perm)
    assert mismatched["result"] == "FAIL" and "runtime identity differs" in mismatched["reason"]

    other_document = {**facts, "document_digest": "sha256:" + "e" * 64}
    monkeypatch.setattr(
        e2e_matrix,
        "wait_for_transition",
        lambda token, operation_id, timeout=600: {"state": "succeeded", "facts": other_document},
    )
    rewritten = e2e_matrix.deploy_shipped_and_wait("t", perm)
    assert rewritten["result"] == "FAIL" and "document digest" in rewritten["reason"]

    monkeypatch.setattr(
        e2e_matrix,
        "wait_for_transition",
        lambda token, operation_id, timeout=600: {
            "state": "failed",
            "failure": {"message": "wiring"},
        },
    )
    assert e2e_matrix.deploy_shipped_and_wait("t", perm)["result"] == "FAIL"

    monkeypatch.setattr(
        e2e_matrix, "deploy_catalog_session", lambda token, perm: {"error": "switch in progress"}
    )
    refused = e2e_matrix.deploy_shipped_and_wait("t", perm)
    assert refused["result"] == "FAIL" and refused["reason"].startswith("Deploy refused")


def test_every_acceptance_lane_deploys_the_shipped_walker_through_the_catalog_contract() -> None:
    source = Path(e2e_matrix.__file__).read_text()
    for name in (
        "run_dirty_repair_acceptance",
        "run_seek_during_mbb_acceptance",
        "run_mbb_acceptance",
        "run_permutation",
    ):
        body = source.split(f"def {name}(")[1].split("\ndef ")[0]
        assert "deploy_shipped_and_wait(" in body, name
        if name != "run_permutation":
            assert "acceptance_permutation(" in body, name
    assert e2e_matrix.MBB_ACCEPTANCE_SESSION_ID == "earth-leo-walker"
    for gone in (
        "deploy_yaml_and_wait",
        "def deploy_session(",
        "_acceptance_session_yaml",
        "_ensure_acceptance_catalog",
        "_prepare_acceptance_session",
        "mbb_overlap_ticks=600",
        "mbb_overlap_ticks=60",
        "TEMPORARY",
    ):
        assert gone not in source, gone
    assert not (ROOT / "tests/fixtures/sessions").exists()
    assert not (ROOT / "tests/fixtures/catalog").exists()


def test_seek_target_aims_ten_seconds_into_a_pending_overlap_or_reports_expiry() -> None:
    sample = {
        "sim_time": "2026-06-08T00:10:00Z",
        "teardown_link": {"scheduling_state": "teardown", "teardown_remaining_ticks": 25},
    }
    target = e2e_matrix._seek_target_for_overlap(sample, overlap_ticks=30, step_seconds=1)  # noqa: SLF001
    assert target["pending"] is True
    assert target["overlap_start_sim_time"] == "2026-06-08T00:09:55+00:00"
    assert target["target_sim_time"] == "2026-06-08T00:10:05+00:00"
    assert target["direction"] == "forward"

    late = {**sample, "teardown_link": {"teardown_remaining_ticks": 10}}
    target = e2e_matrix._seek_target_for_overlap(late, overlap_ticks=30, step_seconds=1)  # noqa: SLF001
    assert target["target_sim_time"] == "2026-06-08T00:09:50+00:00"
    assert target["direction"] == "backward"

    for expired in (
        {**sample, "teardown_link": None},
        {**sample, "teardown_link": {"teardown_remaining_ticks": 0}},
        {**sample, "teardown_link": {"scheduling_state": "teardown"}},
    ):
        verdict = e2e_matrix._seek_target_for_overlap(expired, overlap_ticks=30, step_seconds=1)  # noqa: SLF001
        assert verdict["pending"] is False and "target_sim_time" not in verdict


def test_invalidation_proof_requires_the_pairs_the_epoch_and_the_requested_target() -> None:
    old_pair = ["gs-a", "sat-1"]
    successor_pair = ["gs-a", "sat-2"]
    target = "2026-06-08T00:10:05+00:00"

    def event(**overrides):
        details = {
            "terminal_outcome": "teardown_invalidated_by_epoch",
            "old_pair": old_pair,
            "successor_pair": successor_pair,
            "epoch_id": 4,
            "seek_target_sim_time": "2026-06-08T00:10:05Z",
        }
        details.update(overrides)
        return {"details": details}

    matches = e2e_matrix._invalidation_matches  # noqa: SLF001
    kwargs = {
        "old_pair": old_pair,
        "successor_pair": successor_pair,
        "epoch_id": 4,
        "target": target,
    }
    assert matches(event(), **kwargs)
    assert not matches(event(terminal_outcome="teardown_completed"), **kwargs)
    assert not matches(event(old_pair=["gs-a", "sat-9"]), **kwargs)
    assert not matches(event(epoch_id=5), **kwargs)
    assert not matches(event(seek_target_sim_time="2026-06-08T00:10:35Z"), **kwargs)
    assert not matches(event(seek_target_sim_time=None), **kwargs)
