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


# --- the MBB packet window is a timeline: streaming observers, per-second samples ---


class _FakePopen:
    """A scripted ping process: one (stdout, stderr, returncode) per instance."""

    scripts: list[tuple[str, str, int]] = []
    started: list[dict] = []

    def __init__(self, cmd, **kwargs) -> None:
        import io

        stdout, stderr, rc = type(self).scripts.pop(0)
        type(self).started.append({"cmd": cmd, **kwargs})
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.pid = 4242
        self.returncode = rc

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        raise AssertionError("kill must not be needed for a finished probe")


def _target(node_id: str = "gs-a"):
    return e2e_matrix.WorkloadTarget(
        node_id=node_id, namespace="nodalarc", pod_name=node_id, pod_uid="u", container="frr-router"
    )


_REPLIES = "".join(f"64 bytes from 100.64.0.2: seq={n} ttl=64 time=1.0 ms\n" for n in range(2))


def test_ping_observer_stamps_every_line_and_restarts_after_an_unroutable_exit(monkeypatch) -> None:
    _FakePopen.scripts = [
        (_REPLIES, "ping: sendto: Network unreachable\n", 1),
        (_REPLIES + "2 packets transmitted, 2 packets received, 0% packet loss\n", "", 0),
    ]
    _FakePopen.started = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(e2e_matrix.os, "killpg", lambda pid, sig: None)

    observer = e2e_matrix._PingObserver(  # noqa: SLF001
        "gs-a->gs-b",
        _target(),
        "100.64.0.2",
        count=2,
        interval_s=0.2,
        max_restarts=1,
        restart_delay_s=0,
    )
    observer.start()
    observer._thread.join(timeout=5)  # noqa: SLF001
    assert observer.finished()
    assert observer.restart_limit_reached is True
    assert (
        " exec -n nodalarc gs-a -c frr-router -- ping -c 2 -i 0.2 -W 1 100.64.0.2"
        in observer.command
    )
    assert _FakePopen.started[0]["start_new_session"] is True
    assert [record["instance"] for record in observer.instances] == [0, 1]
    assert [record["returncode"] for record in observer.instances] == [1, 0]
    assert all(line["receipt_wall"] for record in observer.instances for line in record["lines"])

    packets = e2e_matrix._probe_packet_observation(observer.instances)  # noqa: SLF001
    assert packets["instance_count"] == 2 and packets["reply_count"] == 4
    assert packets["packet_outcome"] == "routing_unreachable"
    assert [answer["answer"] for answer in packets["unreachable_answers"]] == [
        "Network unreachable"
    ]
    assert packets["observer_failures"] == []
    assert len(packets["restart_gaps"]) == 1 and packets["restart_gaps"][0]["after_instance"] == 0
    assert packets["protocol_observed"] is True
    # sequences restart with every instance and are kept per instance
    assert [item["reply_seq_ranges"] for item in packets["instances"]] == [[[0, 1]], [[0, 1]]]
    assert "not send times" in packets["note"]


def test_instance_observation_keeps_loss_unreachable_observer_failure_and_silence_apart() -> None:
    def record(lines):
        return {
            "instance": 0,
            "started_wall": "2026-09-15T00:00:00+00:00",
            "ended_wall": "2026-09-15T00:00:05+00:00",
            "returncode": 1,
            "stopped_by_harness": False,
            "lines": [
                {"receipt_wall": "t", "stream": stream, "text": text} for stream, text in lines
            ],
        }

    observe = e2e_matrix._instance_observation  # noqa: SLF001
    lossy = observe(
        record(
            [
                ("stdout", "64 bytes from 100.64.0.2: seq=0 ttl=64 time=1.0 ms"),
                ("stdout", "64 bytes from 100.64.0.2: seq=1 ttl=64 time=1.0 ms"),
                ("stdout", "64 bytes from 100.64.0.2: seq=3 ttl=64 time=1.0 ms"),
            ]
        )
    )
    assert lossy["reply_count"] == 3
    assert lossy["missing_seq_ranges_within_retained_replies"] == [[2, 2]]
    assert lossy["protocol_observed"] is True and lossy["unreachable_answers"] == []

    unreachable = observe(record([("stderr", "ping: sendto: Network is unreachable")]))
    assert unreachable["unreachable_answers"][0]["answer"] == "Network is unreachable"
    assert unreachable["protocol_observed"] is True and unreachable["observer_failures"] == []

    diagnostic = observe(
        record(
            [("stderr", 'error: unable to upgrade connection: container not found ("frr-router")')]
        )
    )
    assert diagnostic["observer_failures"] and diagnostic["protocol_observed"] is False
    assert diagnostic["unreachable_answers"] == []

    silent = observe(record([]))
    assert silent["reply_count"] == 0 and silent["protocol_observed"] is False

    aggregate = e2e_matrix._probe_packet_observation  # noqa: SLF001
    assert (
        aggregate([record([("stderr", "Network is unreachable (kubectl)")])])["packet_outcome"]
        == "probe_error"
    )
    assert aggregate([record([])])["packet_outcome"] == "no_replies"


def test_isis_neighbor_rows_identify_each_adjacency_individually() -> None:
    table = (
        "Area NODAL:\n"
        " System Id           Interface   L  State         Holdtime SNPA\n"
        " space-sat-p00s07    term0       3  Initializing  2        2020.2020.2020\n"
        " space-sat-p00s08    term1       3  Up            3        2020.2020.2020\n"
    )
    rows = e2e_matrix._isis_neighbor_rows(table)  # noqa: SLF001
    assert [(row["system_id"], row["interface"], row["state"]) for row in rows] == [
        ("space-sat-p00s07", "term0", "Initializing"),
        ("space-sat-p00s08", "term1", "Up"),
    ]
    assert e2e_matrix._adjacency_on(rows, "term0")["state"] == "Initializing"  # noqa: SLF001
    assert e2e_matrix._adjacency_on(rows, "term9") is None  # noqa: SLF001
    assert e2e_matrix._adjacency_on(rows, None) is None  # noqa: SLF001


def _sample(*, links, neighbors, route_dev, sim="2026-06-08T00:14:56Z"):
    table = "Area NODAL:\n System Id  Interface  L  State  Holdtime SNPA\n" + "".join(
        f" sat-{iface}   {iface}   3  {state}  3  2020.2020.2020\n" for iface, state in neighbors
    )
    return {
        "sim_time": sim,
        "read_started_wall": "2026-09-15T00:00:00+00:00",
        "read_finished_wall": "2026-09-15T00:00:01+00:00",
        "decision_snapshot_seq": 906,
        "active_ground_links": links,
        "neighbors": e2e_matrix._isis_neighbor_rows(table),  # noqa: SLF001
        "neighbor_observation": {"observed": True, "positive": True},
        "isis_stdout": table,
        "routes": {
            "gs-a->gs-b": {
                "observed": route_dev is not None,
                "positive": route_dev is not None,
                "egress_dev": route_dev,
                "stdout": f"100.64.0.2 dev {route_dev}" if route_dev else "",
            }
        },
    }


_OVERLAP_LINKS = [
    {
        "node_a": "gs-a",
        "node_b": "sat-1",
        "interface_a": "term1",
        "link_reason": "",
        "scheduling_state": "teardown",
        "teardown_remaining_ticks": 20,
    },
    {
        "node_a": "gs-a",
        "node_b": "sat-2",
        "interface_a": "term0",
        "link_reason": "vis_gained",
        "scheduling_state": "active",
    },
]
_PROBE = {"key": "gs-a->gs-b", "src": "gs-a", "dst_gs": "gs-b", "dst_ip": "100.64.0.2"}


def test_overlap_gate_fields_read_the_flow_route_and_the_successors_own_adjacency() -> None:
    gate = e2e_matrix._overlap_gate_fields  # noqa: SLF001
    incumbent_route = gate(
        _sample(
            links=_OVERLAP_LINKS,
            neighbors=[("term0", "Initializing"), ("term1", "Up")],
            route_dev="term1",
        ),
        _PROBE,
    )
    assert incumbent_route["successor_interface"] == "term0"
    assert (
        incumbent_route["route_dev"] == "term1" and incumbent_route["successor_fib_ready"] is False
    )
    # the successor's own adjacency decides neighbor_up, not the incumbent's
    assert incumbent_route["neighbor_up"] is False
    assert incumbent_route["successor_adjacency"]["state"] == "Initializing"
    assert [row["interface"] for row in incumbent_route["incumbent_adjacencies"]] == ["term1"]
    assert e2e_matrix._routing_layer_outcome(incumbent_route) == "successor_adjacency_not_up"  # noqa: SLF001

    successor_route = gate(
        _sample(
            links=_OVERLAP_LINKS, neighbors=[("term0", "Up"), ("term1", "Up")], route_dev="term0"
        ),
        _PROBE,
    )
    assert successor_route["successor_fib_ready"] is True and successor_route["neighbor_up"] is True

    assert (
        gate(
            _sample(links=_OVERLAP_LINKS[:1], neighbors=[("term1", "Up")], route_dev="term1"),
            _PROBE,
        )
        is None
    )


def _lifecycle(
    seq, *, step=905, snapshot=906, outcome="teardown_completed", message="done", epoch=1
):
    return {
        "seq": seq,
        "source": "ome",
        "code": "MBB_TEARDOWN_TERMINAL",
        "timestamp": "2026-09-14T23:53:34.180304Z",
        "details": {
            "session_id": "run-1",
            "epoch_id": epoch,
            "allocator_step": step,
            "snapshot_seq": snapshot,
            "teardown_id": "gs-a:sat-1->gs-a:sat-2",
            "gs_id": "gs-a",
            "terminal_outcome": outcome,
            "message": message,
        },
    }


def test_lifecycle_occurrences_are_scoped_by_run_epoch_step_and_snapshot_and_keep_raw_records() -> (
    None
):
    events = [
        _lifecycle(1),
        _lifecycle(2),  # the same occurrence published twice
        _lifecycle(3, step=990, snapshot=991),  # the same teardown_id recurring later
        _lifecycle(4, step=990, snapshot=991, message="different wording"),  # conflicting duplicate
        {"source": "scheduler", "code": "ACTUATION_CLEAN", "details": {}},
    ]
    groups = e2e_matrix._lifecycle_occurrences(events)  # noqa: SLF001
    assert len(groups) == 2
    first, second = groups
    assert (
        first["record_count"] == 2
        and first["duplicate_records"]
        and not first["conflicting_records"]
    )
    assert [record["seq"] for record in first["records"]] == [1, 2]
    assert second["record_count"] == 2 and second["conflicting_records"] is True
    assert (
        first["occurrence"]["allocator_step"] == 905
        and second["occurrence"]["allocator_step"] == 990
    )


def test_lifecycle_check_counts_distinct_completed_occurrences_not_records(monkeypatch) -> None:
    monkeypatch.setattr(e2e_matrix, "request_json", lambda *a, **k: [_lifecycle(1), _lifecycle(2)])
    result = e2e_matrix.check_mbb_lifecycle_and_ops("t", wait_s=1)
    assert result["result"] == "PASS"
    assert result["completed_count"] == 1
    assert result["lifecycle_record_count"] == 2 and result["lifecycle_occurrence_count"] == 1
    assert len(result["duplicate_record_groups"]) == 1 and result["conflicting_record_groups"] == []


def test_probe_sources_are_the_resolved_limit_one_mbb_stations() -> None:
    perm = e2e_matrix.acceptance_permutation(_PROVENANCE)
    assert e2e_matrix._mbb_probe_sources(perm) == [  # noqa: SLF001
        "earth-de-frankfurt-gw1",
        "earth-us-co-denver-gw2",
        "earth-us-va-ashburn-gw1",
    ]


def test_all_routed_probes_come_from_limit_one_stations_to_other_sites_by_space_egress(
    monkeypatch,
) -> None:
    perm = e2e_matrix.acceptance_permutation(_PROVENANCE)
    nodes = [
        {
            "node_id": gw,
            "node_type": "ground_station",
            "addresses": [
                {"purpose": "router_loopback", "family": "ipv4", "address": f"10.255.0.{n}/32"}
            ],
        }
        for n, gw in enumerate(sorted(perm["ground_topology"]), start=10)
    ] + [{"node_id": "sat-1", "node_type": "satellite"}]
    links = [
        {
            "node_a": gw,
            "node_b": "sat-1",
            "state": "active",
            "interface_a": "term0",
            "interface_b": "gnd0",
        }
        for gw in ("earth-us-va-ashburn-gw1", "earth-us-hawthorne-gw1", "earth-us-co-denver-gw2")
    ]
    monkeypatch.setattr(
        e2e_matrix, "request_json", lambda *a, **k: {"nodes": nodes, "links": links}
    )
    table = "Area NODAL:\n System Id  Interface  L  State  Holdtime SNPA\n sat-1  term0  3  Up  3  2020.2020.2020\n"

    def fake_exec(node_id, command, *, timeout=20):
        if command.startswith("ip route get"):
            dev = "terr0" if node_id == "earth-us-co-denver-gw2" else "term0"
            dst = command.split()[-1]
            return {
                "rc": 0,
                "stdout": f"{dst} via 10.0.0.1 dev {dev} src 10.0.0.2",
                "stderr": "",
                "target": {},
                "resolution_error": None,
            }
        if command.startswith("vtysh"):
            return {"rc": 0, "stdout": table, "stderr": "", "target": {}, "resolution_error": None}
        return {
            "rc": 0,
            "stdout": "1 packets transmitted, 1 packets received, 0% packet loss\n",
            "stderr": "",
            "target": {},
            "resolution_error": None,
        }

    monkeypatch.setattr(e2e_matrix, "_kubectl_exec", fake_exec)

    probes = e2e_matrix._find_all_routed_ground_probes("t", perm)  # noqa: SLF001

    assert [probe["src"] for probe in probes] == ["earth-us-va-ashburn-gw1"]
    probe = probes[0]
    assert probe["dst_site"] != probe["src_site"] and probe["egress_dev"] == "term0"
    assert probe["transit_proven"] is True and probe["steady_limit"] == 1
    # Denver gw2 routed over the site LAN: excluded; Hawthorne has steady limit 7: never a source.


class _FakeObserver:
    def __init__(self, key, target, dst_ip, *, count, interval_s, **kwargs) -> None:
        self.key = key
        self.instances = [
            {
                "instance": 0,
                "started_wall": "2026-09-15T00:00:00+00:00",
                "ended_wall": "2026-09-15T00:00:09+00:00",
                "returncode": 0,
                "stopped_by_harness": True,
                "lines": [
                    {
                        "receipt_wall": "t",
                        "stream": "stdout",
                        "text": f"64 bytes from {dst_ip}: seq={n} ttl=64 time=1.0 ms",
                    }
                    for n in range(5)
                ],
            }
        ]
        self.restart_limit_reached = False
        self.command = f"ping {dst_ip}"
        self.stopped = False

    def start(self):
        pass

    def finished(self):
        return self.stopped

    def stop(self, *, grace_s=15.0):
        self.stopped = True


def test_packet_window_grades_the_first_overlap_sample_and_never_a_post_teardown_one(
    monkeypatch,
) -> None:
    import itertools

    perm = {
        "ground_topology": {"gs-a": {}},
        "mbb_stations": {"gs-a": {"steady_limit": 1, "handover_mode": "mbb"}},
    }
    before = [
        _sample(
            links=_OVERLAP_LINKS[1:],
            neighbors=[("term1", "Up")],
            route_dev="term1",
            sim="2026-06-08T00:14:50Z",
        ),
        _sample(
            links=_OVERLAP_LINKS, neighbors=[("term0", "Up"), ("term1", "Up")], route_dev="term1"
        ),
        _sample(
            links=_OVERLAP_LINKS,
            neighbors=[("term0", "Up"), ("term1", "Up")],
            route_dev="term1",
            sim="2026-06-08T00:14:57Z",
        ),
    ]
    after = _sample(
        links=_OVERLAP_LINKS[1:],
        neighbors=[("term0", "Up")],
        route_dev="term0",
        sim="2026-06-08T00:15:06Z",
    )
    samples = itertools.chain(before, itertools.repeat(after))
    lifecycle = [_lifecycle(1), _lifecycle(2)]
    event_batches = itertools.chain([[], []], itertools.repeat(lifecycle))
    monkeypatch.setattr(e2e_matrix, "_find_all_routed_ground_probes", lambda token, perm: [_PROBE])
    monkeypatch.setattr(e2e_matrix, "_workload_target", lambda node_id: (_target(), None))
    monkeypatch.setattr(e2e_matrix, "_PingObserver", _FakeObserver)
    monkeypatch.setattr(
        e2e_matrix,
        "_sample_station",
        lambda token, src, probes, protocol="isis": dict(next(samples)),
    )
    monkeypatch.setattr(
        e2e_matrix,
        "request_json",
        lambda method, path, **k: next(event_batches) if "ops/events" in path else [],
    )
    monkeypatch.setattr(
        e2e_matrix,
        "_link_events_for",
        lambda token, nodes, *, start_sim: [{"event_type": "link_down", "node_a": "gs-a"}],
    )
    monkeypatch.setattr(e2e_matrix, "_event_at_or_after", lambda event, started_at: True)
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _s: None)

    result = e2e_matrix._run_mbb_packet_window(  # noqa: SLF001
        "t", perm, count=5, interval_s=0.2, post_terminal_s=0.05
    )

    assert result["result"] == "FAIL"
    # the gate's input is the first overlap sighting (index 1), route still on the incumbent
    assert result["overlap_proof"]["sample_index"] == 1
    assert result["overlap_proof"]["successor_fib_ready"] is False
    assert result["routing_layer_outcome"] == "fib_still_points_to_other_interface"
    # the route moved to the successor after the teardown: recorded, never gating
    post = result["post_teardown_route_observation"]
    assert post["egress_dev"] == "term0" and post["sample_index"] >= 3
    assert "never satisfies" in post["note"]
    assert result["terminal_event"]["seq"] == 1
    assert result["terminal_observation"]["sample_index_at_receipt"] == 3
    assert [sample["index"] for sample in result["timeline"][:4]] == [0, 1, 2, 3]
    assert len(result["timeline"]) >= 4
    assert len(result["ops_events"]) == 2 and len(result["lifecycle_occurrences"]) == 1
    assert result["lifecycle_occurrences"][0]["duplicate_records"] is True
    assert result["link_events"] == [{"event_type": "link_down", "node_a": "gs-a"}]
    assert result["probe_outputs"]["gs-a->gs-b"]["reply_count"] == 5
    assert result["collector"]["sample_count"] == len(result["timeline"])
    assert result["collector"]["cadence_s"] == 1.0
    assert result["packet_loss_policy"] == "recorded_not_gated"


def test_packet_window_starts_nothing_when_a_target_is_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(e2e_matrix, "_find_all_routed_ground_probes", lambda token, perm: [_PROBE])
    monkeypatch.setattr(
        e2e_matrix,
        "_workload_target",
        lambda node_id: (None, "expected one live session pod, found 0: []"),
    )
    monkeypatch.setattr(
        e2e_matrix,
        "_PingObserver",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no observer")),
    )

    result = e2e_matrix._run_mbb_packet_window("t", {}, count=2, interval_s=0.2)  # noqa: SLF001

    assert result["result"] == "FAIL" and result["failure_kind"] == "probe"
    assert "expected one live session pod" in result["reason"]


def test_packet_behavior_retains_every_attempt_in_full(monkeypatch) -> None:
    windows = iter(
        [
            {
                "result": "FAIL",
                "reason": "No probed station completed an MBB teardown during the packet window",
                "timeline": [{"index": 0}],
                "terminal_event": None,
            },
            {
                "result": "PASS",
                "timeline": [{"index": 0}, {"index": 1}],
                "terminal_event": {"seq": 9},
            },
        ]
    )
    monkeypatch.setattr(
        e2e_matrix,
        "_run_mbb_packet_window",
        lambda token, perm, *, count, interval_s: next(windows),
    )
    monkeypatch.setattr(e2e_matrix.time, "sleep", lambda _s: None)

    result = e2e_matrix.check_mbb_packet_behavior("t", {}, max_wait_s=900)

    assert result["result"] == "PASS"
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["result"] == "FAIL" and result["attempts"][0]["timeline"] == [
        {"index": 0}
    ]


def test_station_sampler_reads_adjacency_and_route_through_the_observation_parsers() -> None:
    source = Path(e2e_matrix.__file__).read_text()
    sampler = source.split("def _sample_station")[1].split("\ndef ")[0]
    window = source.split("def _run_mbb_packet_window")[1].split("\ndef ")[0]
    assert "_adjacency_observation(" in sampler and "_route_observation(" in sampler
    assert '"Up" in' not in window and '"Up" in' not in sampler
    assert source.count("def _route_egress_dev") == 1 and "def _route_dev" not in source
    # the gate's overlap input is fixed at first sighting, before the terminal event
    assert "src in terminal_by_src:\n                    continue" in window
