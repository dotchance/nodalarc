# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for the e2e matrix acceptance helpers.

These tests exercise the cluster-run script in the normal unit suite: every
session requires routed proof, and the MBB lane records packet loss as routing
behavior.
"""

from __future__ import annotations

from pathlib import Path

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


_OVERLAP_STARTED = [
    {
        "category": "mbb_overlap_started",
        "pair": ["gs-a", "sat-1"],
        "successor_pair": ["gs-a", "sat-2"],
        "message": "MBB overlap started for incumbent ('gs-a', 'sat-1') with successor ('gs-a', 'sat-2'); overlap_ticks=30",
    }
]


def _pairs_of(links):
    return sorted(sorted((str(link["node_a"]), str(link["node_b"]))) for link in links)


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


def _instance(n, lines):
    return {
        "instance": n,
        "started_wall": f"2026-09-15T00:0{n}:00+00:00",
        "ended_wall": f"2026-09-15T00:0{n}:30+00:00",
        "returncode": 0,
        "stopped_by_harness": False,
        "lines": [{"receipt_wall": "t", "stream": stream, "text": text} for stream, text in lines],
    }


def test_ping_statistics_are_parsed_numerically_not_by_substring() -> None:
    parse = e2e_matrix._parse_ping_statistics  # noqa: SLF001
    assert parse(_PING_ZERO_LOSS)["loss_class"] == "zero_loss"
    assert parse(_PING_TOTAL_LOSS)["loss_class"] == "total_loss"
    assert parse(_PING_PARTIAL_LOSS)["loss_class"] == "partial_loss"
    assert parse("PING x\n64 bytes from 100.64.0.17: seq=0 ttl=64 time=1 ms\n") is None
    # The old substring rule called both of these zero loss.
    assert "0% packet loss" in _PING_TOTAL_LOSS and "0% packet loss" in _PING_PARTIAL_LOSS

    # the observer path reads the same numbers: loss is counted, never inferred
    def observed(stdout: str) -> dict:
        return e2e_matrix._instance_observation(  # noqa: SLF001
            _instance(0, [("stdout", line) for line in stdout.splitlines()])
        )

    assert observed(_PING_TOTAL_LOSS)["measured_loss"] is True
    assert observed(_PING_TOTAL_LOSS)["statistics"]["loss_class"] == "total_loss"
    assert observed(_PING_PARTIAL_LOSS)["measured_loss"] is True
    assert observed(_PING_ZERO_LOSS)["measured_loss"] is False


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


def test_ping_statistics_must_be_consistent_numbers_from_a_transmission() -> None:
    parse = e2e_matrix._parse_ping_statistics  # noqa: SLF001
    obs = e2e_matrix._packet_observation  # noqa: SLF001
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
        # in the window the same line invalidates the instance's measurement
        window = e2e_matrix._instance_observation(_instance(0, [("stdout", line)]))  # noqa: SLF001
        assert window["protocol_observed"] is False, line
        assert any("inconsistent ping statistics" in f["kind"] for f in window["observer_failures"])
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
        == "observer_error"
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


def _link_line(ifname: str, *, up: bool) -> str:
    flags = "BROADCAST,MULTICAST,UP,LOWER_UP" if up else "BROADCAST,MULTICAST,UP"
    state = "UP" if up else "LOWERLAYERDOWN"
    return f"5: {ifname}@if12: <{flags}> mtu 1500 qdisc noqueue state {state} mode DEFAULT"


def _parse_link(line: str):
    parse = getattr(e2e_matrix, "_parse_link_line", None)
    return parse(line) if parse else None


def _sample(
    *,
    links,
    neighbors,
    route_dev,
    sim="2026-06-08T00:14:56Z",
    incumbent_up=True,
    allocation_events=None,
    actual=None,
):
    table = "Area NODAL:\n System Id  Interface  L  State  Holdtime SNPA\n" + "".join(
        f" sat-{iface}   {iface}   3  {state}  3  2020.2020.2020\n" for iface, state in neighbors
    )
    bracket = {
        "session_id": "run-1",
        "epoch_id": 1,
        "sim_time": sim,
        "decision_snapshot_seq": 906,
        "active_ground_links": links,
        "allocation_events": _OVERLAP_STARTED if allocation_events is None else allocation_events,
        "kernel_actual_pairs": _pairs_of(links) if actual is None else list(actual),
        "scheduler_instance_ids": ["inst-1"],
    }
    return {
        "session_id": "run-1",
        "epoch_id": 1,
        "sim_time": sim,
        "allocation_events": bracket["allocation_events"],
        "read_started_wall": "2026-09-15T00:00:00+00:00",
        "read_finished_wall": "2026-09-15T00:00:01+00:00",
        "kernel_read_started_wall": "2026-09-15T00:00:00.2+00:00",
        "kernel_read_finished_wall": "2026-09-15T00:00:00.8+00:00",
        "decision_snapshot_seq": 906,
        "active_ground_links": links,
        "pre": dict(bracket),
        "post": dict(bracket),
        "kernel_after_route": {
            "gs-a->gs-b": {
                "incumbent_interface": "term1",
                "successor_interface": "term0",
                "incumbent_sat": "sat-1",
                "incumbent_link": _parse_link(_link_line("term1", up=incumbent_up)),
                "incumbent_link_read": {
                    "rc": 0,
                    "stdout": _link_line("term1", up=incumbent_up),
                    "stderr": "",
                },
                "successor_link": _parse_link(_link_line("term0", up=True)),
                "successor_link_read": {
                    "rc": 0,
                    "stdout": _link_line("term0", up=True),
                    "stderr": "",
                },
                "neighbors_after_route": e2e_matrix._isis_neighbor_rows(table),  # noqa: SLF001
                "neighbor_read": {"rc": 0, "stdout": table, "stderr": ""},
                "neighbor_observation_after_route": {"observed": True, "positive": True},
                "incumbent_adjacency_after_route": (
                    {"system_id": "sat-1", "interface": "term1", "level": "3", "state": "Up"}
                    if incumbent_up
                    else None
                ),
            }
        },
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
        "state": "active",
        "interface_a": "term1",
        "link_reason": "",
        "scheduling_state": "teardown",
        "teardown_remaining_ticks": 20,
    },
    {
        "node_a": "gs-a",
        "node_b": "sat-2",
        "state": "active",
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
    seq,
    *,
    step=905,
    snapshot=910,
    outcome="teardown_completed",
    message="done",
    epoch=1,
    old_pair=("gs-a", "sat-1"),
    successor_pair=("gs-a", "sat-2"),
    master_sim_time="2026-06-08T00:15:05Z",
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
            "master_sim_time": master_sim_time,
            "teardown_id": f"{old_pair[0]}:{old_pair[1]}->{successor_pair[0]}:{successor_pair[1]}",
            "old_pair": list(old_pair),
            "successor_pair": list(successor_pair),
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


def test_probe_sources_are_the_resolved_limit_one_mbb_stations() -> None:
    perm = e2e_matrix.acceptance_permutation(_PROVENANCE)
    assert e2e_matrix._mbb_probe_sources(perm) == [  # noqa: SLF001
        "earth-de-frankfurt-gw1",
        "earth-us-co-denver-gw2",
        "earth-us-va-ashburn-gw1",
    ]

    # Denver gw2 routed over the site LAN: excluded; Hawthorne has steady limit 7: never a source.


_READY_OVERLAP = _sample(
    links=_OVERLAP_LINKS, neighbors=[("term0", "Up"), ("term1", "Up")], route_dev="term0"
)


def test_every_stamped_reply_and_raw_line_is_retained() -> None:
    lines = [
        {
            "receipt_wall": f"2026-09-15T00:00:0{n}+00:00",
            "stream": "stdout",
            "text": f"64 bytes from 100.64.0.2: seq={n} ttl=64 time=1.0 ms",
        }
        for n in range(3)
    ]
    record = {
        "instance": 0,
        "started_wall": "s",
        "ended_wall": "e",
        "returncode": 0,
        "stopped_by_harness": False,
        "lines": lines,
    }
    observation = e2e_matrix._instance_observation(record)  # noqa: SLF001
    assert [reply["receipt_wall"] for reply in observation["replies"]] == [
        "2026-09-15T00:00:00+00:00",
        "2026-09-15T00:00:01+00:00",
        "2026-09-15T00:00:02+00:00",
    ]
    assert observation["raw_lines"] == lines


def _reply_lines(count):
    return [
        ("stdout", f"64 bytes from 100.64.0.2: seq={n} ttl=64 time=1.0 ms") for n in range(count)
    ]


def test_packet_classes_stay_distinct_and_an_invalid_measurement_never_qualifies() -> None:
    aggregate = e2e_matrix._probe_packet_observation  # noqa: SLF001

    # two complete zero-loss instances: no loss was measured; the restart is an unobserved gap
    two = aggregate([_instance(0, _reply_lines(5)), _instance(1, _reply_lines(5))])
    assert two["packet_outcome"] == "no_loss_measured"
    assert two["measured_loss"] is False and two["unobserved_gap_count"] == 1
    assert two["protocol_observed"] is True

    # an unrecognized ping error is an observer failure, never dropped
    odd = aggregate(
        [_instance(0, _reply_lines(2) + [("stderr", "ping: sendto: Operation not permitted")])]
    )
    assert len(odd["observer_failures"]) == 1
    assert odd["unreachable_answers"] == [] and odd["protocol_observed"] is False
    assert odd["packet_outcome"] == "observer_error"

    # replies followed by a kubectl failure do not qualify the gate input
    broken = aggregate(
        [_instance(0, _reply_lines(3) + [("stderr", "error: unable to upgrade connection")])]
    )
    assert broken["reply_count"] == 3 and broken["protocol_observed"] is False
    assert broken["packet_outcome"] == "observer_error" and broken["measurement_valid"] is False

    # measured loss and explicit unreachable answers stay their own classes
    lossy = aggregate(
        [
            _instance(
                0,
                _reply_lines(2)
                + [("stdout", "64 bytes from 100.64.0.2: seq=4 ttl=64 time=1.0 ms")],
            )
        ]
    )
    assert lossy["measured_loss"] is True and lossy["packet_outcome"] == "measured_loss"
    unreachable = aggregate(
        [_instance(0, _reply_lines(2) + [("stderr", "ping: sendto: Network is unreachable")])]
    )
    assert unreachable["packet_outcome"] == "routing_unreachable"
    assert [a["answer"] for a in unreachable["unreachable_answers"]] == ["Network is unreachable"]
    glibc = aggregate([_instance(0, [("stderr", "ping: sendto: No route to host")])])
    assert glibc["unreachable_answers"][0]["answer"] == "No route to host"
    # a kubectl diagnostic carrying the same words is not a routing answer
    diag = aggregate([_instance(0, [("stderr", "error: Network is unreachable (kubectl)")])])
    assert diag["unreachable_answers"] == [] and diag["observer_failures"]


# --- the kernel observation itself must precede the teardown's enactment ---


def test_binding_reports_uncertainty_or_missing_identity_instead_of_readiness() -> None:
    bind = e2e_matrix._bind_overlap_to_terminal  # noqa: SLF001
    gate = e2e_matrix._overlap_gate_fields  # noqa: SLF001
    event = _lifecycle(1)
    ready = gate(_READY_OVERLAP, _PROBE)
    assert bind(ready, event, [])["bound"] is True

    # the incumbent's carrier was already down when read after the route
    down = gate(
        _sample(
            links=_OVERLAP_LINKS,
            neighbors=[("term0", "Up")],
            route_dev="term0",
            incumbent_up=False,
        ),
        _PROBE,
    )
    assert bind(down, event, [])["reason"] == "overlap_ordering_uncertain"
    # another satellite's adjacency on the incumbent's interface: not the incumbent
    other_sat = {
        **ready,
        "kernel_after_route": {
            **ready["kernel_after_route"],
            "incumbent_adjacency_after_route": {
                "system_id": "sat-9",
                "interface": "term1",
                "level": "3",
                "state": "Up",
            },
        },
    }
    assert bind(other_sat, event, [])["reason"] == "overlap_ordering_uncertain"
    # no kernel-level evidence at all
    no_kernel = {**ready, "kernel_after_route": None}
    assert bind(no_kernel, event, [])["reason"] == "overlap_ordering_uncertain"
    # the pre reading already carried the teardown: this cannot be the graded overlap
    late_pre = {**ready, "pre": {**ready["pre"], "decision_snapshot_seq": 911}}
    assert bind(late_pre, event, [])["reason"] == "overlap_ordering_uncertain"
    # identity: the post reading's run and epoch count too
    for field, value, reason in (
        ("session_id", "run-2", "overlap_identity_mismatch"),
        ("session_id", None, "overlap_identity_missing"),
        ("epoch_id", 2, "overlap_identity_mismatch"),
        ("epoch_id", None, "overlap_identity_missing"),
    ):
        changed = {**ready, "post": {**ready["post"], field: value}}
        assert bind(changed, event, [])["reason"] == reason, (field, value)
    other_run = _lifecycle(1)
    other_run["details"]["session_id"] = "run-2"
    assert bind(ready, other_run, [])["reason"] == "overlap_identity_mismatch"
