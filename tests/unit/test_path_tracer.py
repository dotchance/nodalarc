"""A path trace reports what traceroute measured, and nothing else."""

from __future__ import annotations

import pytest
from vs_api.path_tracer import PathTracer, UntraceableNodeError
from vs_api.resolved_runtime_views import TracerNode

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


def test_an_address_assigned_to_two_nodes_is_refused() -> None:
    with pytest.raises(ValueError, match="10.0.0.1 is assigned to sat-a and sat-c"):
        PathTracer(
            node_registry={**REGISTRY, "sat-c": _node("sat-c", "10.0.0.1")},
            namespace="nodalarc",
            core_v1=lambda: None,
            read_sim_time=lambda: SIM_TIME,
        )


def test_a_host_node_is_traced_from_its_gateway() -> None:
    tracer = PathTracer(
        node_registry=REGISTRY,
        namespace="nodalarc",
        core_v1=lambda: None,
        read_sim_time=lambda: SIM_TIME,
    )

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
