"""Tests for PathResult and PathQuery shared models."""

from nodalarc.models.path import PathHop, PathQuery, PathResult


def test_path_hop_valid():
    hop = PathHop(
        node_id="sat-P00S00",
        node_type="satellite",
        in_label=100,
        out_label=200,
        action="swap",
        out_interface="isl0",
        latency_to_next_ms=5.3,
    )
    assert hop.action == "swap"


def test_path_result_reachable():
    result = PathResult(
        src="gs-ashburn",
        dst="gs-frankfurt",
        hops=[],
        total_latency_ms=42.0,
        method="derived",
        sim_time="2026-01-01T00:01:00Z",
        topology_state_id="s1",
        reachable=True,
    )
    assert result.reachable is True
    assert result.method == "derived"


def test_path_result_unreachable():
    result = PathResult(
        src="gs-ashburn",
        dst="gs-frankfurt",
        hops=[],
        total_latency_ms=0.0,
        method="derived",
        sim_time="",
        topology_state_id="",
        reachable=False,
        unreachable_reason="no ingress rule",
    )
    assert result.unreachable_reason == "no ingress rule"


def test_path_query_optional_sim_time():
    q = PathQuery(src="gs-ashburn", dst="gs-frankfurt")
    assert q.sim_time is None

    q2 = PathQuery(src="gs-a", dst="gs-b", sim_time="2026-01-01T00:00:00Z")
    assert q2.sim_time is not None
