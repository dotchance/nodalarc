"""A path trace reports what traceroute measured, and nothing else."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nodalarc.workload_target import NODE_ID_LABEL
from vs_api.path_tracer import PathTracer, UntraceableNodeError
from vs_api.resolved_runtime_views import TracerNode

from tests.unit.test_workload_target import pod_document

SIM_TIME = "2026-03-13T10:00:00+00:00"


def _node(
    node_id: str, loopback: str, gateway: str | None = None, lan: tuple[str, ...] = ()
) -> TracerNode:
    return TracerNode(
        node_id=node_id,
        loopback_ipv4=loopback,
        addresses_ipv4=(loopback, *lan),
        trace_gateway_node_id=gateway,
    )


REGISTRY = {
    "gs-alpha": _node("gs-alpha", "10.2.0.1", lan=("172.16.1.1",)),
    "sat-a": _node("sat-a", "10.0.0.1"),
    "sat-b": _node("sat-b", "10.0.0.2"),
    "gs-beta": _node("gs-beta", "10.2.1.1"),
    "site-host": _node("site-host", "10.9.0.1", gateway="gs-alpha"),
}


class _Stream:
    """A finished exec whose stdout arrives in chunks."""

    def __init__(self, chunks: list[str], *, returncode: int = 0, stderr: str = "") -> None:
        self._chunks = list(chunks)
        self._stderr = stderr
        self.returncode = returncode
        self.closed = False

    def is_open(self) -> bool:
        return bool(self._chunks)

    def update(self, timeout: int) -> None:
        pass

    def read_stdout(self) -> str:
        return self._chunks.pop(0) if self._chunks else ""

    def read_stderr(self) -> str:
        stderr, self._stderr = self._stderr, ""
        return stderr

    def close(self) -> None:
        self.closed = True


class _Cluster:
    """CoreV1 pod listing for the registry's nodes; exec output per origin node."""

    def __init__(self, outputs: dict[str, _Stream], missing: frozenset[str] = frozenset()):
        self.outputs = outputs
        self.missing = missing

    def list_namespaced_pod(self, namespace: str, *, label_selector: str):
        node_id = label_selector.removeprefix(f"{NODE_ID_LABEL}=")
        if node_id in self.missing:
            return SimpleNamespace(items=[])
        return SimpleNamespace(items=[pod_document(node_id, primary="frr", containers=("frr",))])

    def connect_get_namespaced_pod_exec(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("exec goes through kubernetes.stream.stream")

    def stream(self, _exec, pod_name: str, _namespace: str, **_kwargs):
        return self.outputs[pod_name]


FLOW_ID = "flow"


def _tracer(cluster: _Cluster) -> PathTracer:
    return PathTracer(
        node_registry=REGISTRY,
        namespace="nodalarc",
        core_v1=lambda: cluster,
        read_sim_time=lambda: SIM_TIME,
    )


def _trace_once(tracer: PathTracer, cluster: _Cluster, src: str, dst: str, on_progress=None):
    with patch("kubernetes.stream.stream", side_effect=cluster.stream):
        return tracer.trace(
            tracer.endpoint(src),
            tracer.endpoint(dst),
            flow_id=FLOW_ID,
            tracing=True,
            on_progress=on_progress,
        )


def _output(*hop_lines: str) -> list[str]:
    return ["traceroute to x (x), 20 hops max, 46 byte packets\n", *(f"{h}\n" for h in hop_lines)]


def test_both_directions_reached_report_the_destination_round_trip_and_symmetry() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(
                _output(" 1  10.0.0.1  5.0 ms", " 2  10.0.0.2  9.0 ms", " 3  10.2.1.1  14.0 ms")
            ),
            "gs-beta": _Stream(
                _output(" 1  10.0.0.2  4.0 ms", " 2  10.0.0.1  8.0 ms", " 3  10.2.0.1  13.0 ms")
            ),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.flow_id == FLOW_ID
    assert result.hops == ["gs-alpha", "sat-a", "sat-b", "gs-beta"]
    assert result.hop_rtts == [None, 5.0, 9.0, 14.0]
    assert (result.state, result.rtt_ms, result.error) == ("reached", 14.0, None)
    assert result.reverse_hops == ["gs-beta", "sat-b", "sat-a", "gs-alpha"]
    assert (result.reverse_state, result.reverse_rtt_ms) == ("reached", 13.0)
    assert result.asymmetry_detected is False
    assert result.tracing is True
    assert result.sim_time == SIM_TIME


def test_different_nodes_between_the_ends_are_asymmetry() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(_output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms")),
            "gs-beta": _Stream(_output(" 1  10.0.0.2  4.0 ms", " 2  10.2.0.1  8.0 ms")),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.asymmetry_detected is True


def test_a_hop_answering_from_a_node_lan_address_is_that_node() -> None:
    cluster = _Cluster(
        {
            "gs-beta": _Stream(
                _output(" 1  10.0.0.2  4.0 ms", " 2  172.16.1.1  8.0 ms", " 3  10.2.0.1  8.1 ms")
            ),
            "gs-alpha": _Stream(_output(" 1  10.2.1.1  8.0 ms")),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-beta", "gs-alpha")

    assert result.hops == ["gs-beta", "sat-b", "gs-alpha", "gs-alpha"]


def test_an_address_assigned_to_two_nodes_is_refused() -> None:
    with pytest.raises(ValueError, match="10.0.0.1 is assigned to sat-a and sat-c"):
        PathTracer(
            node_registry={**REGISTRY, "sat-c": _node("sat-c", "10.0.0.1")},
            namespace="nodalarc",
            core_v1=lambda: None,
            read_sim_time=lambda: SIM_TIME,
        )


def test_silent_and_unknown_hops_stay_in_the_path_and_leave_asymmetry_unknown() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(_output(" 1  *", " 2  192.0.2.7  9.0 ms", " 3  10.2.1.1  14.0 ms")),
            "gs-beta": _Stream(
                _output(" 1  10.0.0.2  4.0 ms", " 2  10.0.0.1  8.0 ms", " 3  10.2.0.1  13.0 ms")
            ),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.hops == ["gs-alpha", "*", "192.0.2.7", "gs-beta"]
    assert result.hop_rtts == [None, None, 9.0, 14.0]
    assert result.asymmetry_detected is None


def test_a_trace_that_ends_short_of_the_destination_did_not_reach_it() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(_output(" 1  10.0.0.1  5.0 ms", " 2  *", " 3  *")),
            "gs-beta": _Stream(_output(" 1  10.0.0.2  4.0 ms", " 2  10.2.0.1  8.0 ms")),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert (result.state, result.rtt_ms, result.error) == ("not_reached", None, None)
    assert result.hops == ["gs-alpha", "sat-a", "*", "*"]
    assert result.reverse_state == "reached"
    assert result.asymmetry_detected is None


def test_a_node_without_a_workload_fails_its_direction_with_the_reason() -> None:
    cluster = _Cluster(
        {"gs-beta": _Stream(_output(" 1  10.2.0.1  8.0 ms"))},
        missing=frozenset({"gs-alpha"}),
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.state == "failed"
    assert result.hops == ["gs-alpha"]
    assert result.rtt_ms is None
    assert "gs-alpha: expected one live session pod, found 0" in result.error
    assert result.reverse_state == "reached"


def test_a_traceroute_that_exits_nonzero_fails_with_its_own_words() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(
                [], returncode=1, stderr="traceroute: sendto: Network unreachable\n"
            ),
            "gs-beta": _Stream(_output(" 1  10.2.0.1  8.0 ms")),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.state == "failed"
    assert result.error == (
        "traceroute exited 1 in gs-alpha: traceroute: sendto: Network unreachable"
    )
    assert cluster.outputs["gs-alpha"].closed


def test_unreadable_output_fails_the_direction() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(["bogus line\n"]),
            "gs-beta": _Stream(_output(" 1  10.2.0.1  8.0 ms")),
        }
    )

    result = _trace_once(_tracer(cluster), cluster, "gs-alpha", "gs-beta")

    assert result.state == "failed"
    assert result.error == "unreadable traceroute output: not a traceroute hop line: 'bogus line'"


def test_the_forward_path_is_published_while_it_grows() -> None:
    seen: list[tuple[str, list[str], str]] = []
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(
                _output(" 1  10.0.0.1  5.0 ms", " 2  10.0.0.2  9.0 ms", " 3  10.2.1.1  14.0 ms")
            ),
            "gs-beta": _Stream(_output(" 1  10.2.0.1  8.0 ms")),
        }
    )

    _trace_once(
        _tracer(cluster),
        cluster,
        "gs-alpha",
        "gs-beta",
        on_progress=lambda progress: seen.append(
            (progress.state, progress.hops, progress.reverse_state)
        ),
    )

    assert seen == [
        ("running", ["gs-alpha", "sat-a"], "running"),
        ("running", ["gs-alpha", "sat-a", "sat-b"], "running"),
        ("running", ["gs-alpha", "sat-a", "sat-b", "gs-beta"], "running"),
    ]


def test_a_one_shot_trace_is_not_retraced() -> None:
    cluster = _Cluster(
        {
            "gs-alpha": _Stream(_output(" 1  10.2.1.1  9.0 ms")),
            "gs-beta": _Stream(_output(" 1  10.2.0.1  8.0 ms")),
        }
    )
    tracer = _tracer(cluster)

    with patch("kubernetes.stream.stream", side_effect=cluster.stream):
        result = tracer.trace_between("gs-alpha", "gs-beta", flow_id="__trace__")

    assert (result.flow_id, result.tracing) == ("__trace__", False)
    assert (result.state, result.rtt_ms, result.reverse_state) == ("reached", 9.0, "reached")


def test_the_workload_is_read_on_every_trace() -> None:
    cluster = _Cluster({})
    listings = [
        [pod_document("gs-alpha", uid="uid-1", primary="frr-router", containers=("frr-router",))],
        [
            pod_document(
                "gs-alpha",
                uid="uid-1",
                deleting=True,
                primary="frr-router",
                containers=("frr-router",),
            ),
            pod_document(
                "gs-alpha", uid="uid-2", primary="custom-router", containers=("custom-router",)
            ),
        ],
    ]
    cluster.list_namespaced_pod = lambda namespace, *, label_selector: SimpleNamespace(
        items=listings.pop(0)
    )
    tracer = _tracer(cluster)

    with patch("kubernetes.stream.stream", return_value=_Stream([])) as stream:
        tracer._run_traceroute("gs-alpha", "10.2.1.1", lambda _stdout: None)
        stream.return_value = _Stream([])
        tracer._run_traceroute("gs-alpha", "10.2.1.1", lambda _stdout: None)

    assert [call.kwargs["container"] for call in stream.call_args_list] == [
        "frr-router",
        "custom-router",
    ]


def test_a_host_node_is_traced_from_its_gateway() -> None:
    tracer = _tracer(_Cluster({}))

    endpoint = tracer.endpoint("site-host")

    assert endpoint.node.node_id == "site-host"
    assert endpoint.runs_from.node_id == "gs-alpha"


def test_a_node_without_a_loopback_cannot_be_traced() -> None:
    tracer = PathTracer(
        node_registry={"site-host": _node("site-host", "10.9.0.1", gateway="gs-gone")},
        namespace="nodalarc",
        core_v1=lambda: None,
        read_sim_time=lambda: SIM_TIME,
    )

    with pytest.raises(UntraceableNodeError, match="gateway gs-gone"):
        tracer.endpoint("site-host")
    with pytest.raises(UntraceableNodeError, match="ghost has no loopback"):
        tracer.endpoint("ghost")
