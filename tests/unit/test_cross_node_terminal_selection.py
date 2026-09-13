"""The cross-node terminal probe chooses its target through the published pod contracts."""

from __future__ import annotations

from nodalarc.workload_target import (
    NODE_ID_LABEL,
    PRIMARY_CONTAINER_ANNOTATION,
    TERMINAL_ACCESS_ANNOTATION,
)

from tests.integration.test_cross_node_terminal import eligible_ssh_target

VS_API_NODE = "node01"


def _pod(
    name: str,
    *,
    node: str,
    containers: tuple[str, ...] = ("frr-router", "observer"),
    primary: str | None = "frr-router",
    terminal: str | None = '{"surface":"ssh"}',
    pod_ip: str | None = "10.42.1.7",
    deleting: bool = False,
) -> dict:
    annotations: dict[str, str] = {}
    if primary is not None:
        annotations[PRIMARY_CONTAINER_ANNOTATION] = primary
    if terminal is not None:
        annotations[TERMINAL_ACCESS_ANNOTATION] = terminal
    metadata: dict = {
        "name": name,
        "namespace": "nodalarc",
        "uid": f"uid-{name}",
        "labels": {NODE_ID_LABEL: name},
        "annotations": annotations,
    }
    if deleting:
        metadata["deletionTimestamp"] = "2026-09-13T00:00:00Z"
    return {
        "metadata": metadata,
        "spec": {"nodeName": node, "containers": [{"name": c} for c in containers]},
        "status": {"podIP": pod_ip} if pod_ip else {},
    }


def test_profile_named_router_on_another_node_is_chosen_with_its_published_container() -> None:
    target, refusals = eligible_ssh_target(
        [_pod("leo-sat-p00s00", node=VS_API_NODE), _pod("leo-sat-p01s03", node="node03")],
        vs_api_node=VS_API_NODE,
    )

    assert target is not None
    assert (target.node_id, target.pod_name, target.node, target.container, target.pod_ip) == (
        "leo-sat-p01s03",
        "leo-sat-p01s03",
        "node03",
        "frr-router",
        "10.42.1.7",
    )
    assert refusals == ("leo-sat-p00s00: on the VS-API node 'node01'",)


def test_pods_on_the_vs_api_node_or_unscheduled_are_refused() -> None:
    target, refusals = eligible_ssh_target(
        [_pod("a", node=VS_API_NODE), _pod("b", node="")],
        vs_api_node=VS_API_NODE,
    )

    assert target is None
    assert refusals == ("a: on the VS-API node 'node01'", "b: not scheduled")


def test_exec_surface_and_absent_terminal_contract_are_refused() -> None:
    target, refusals = eligible_ssh_target(
        [
            _pod(
                "app-node",
                node="node02",
                containers=("app",),
                primary="app",
                terminal='{"surface":"exec","container":"app","command":["sh"]}',
            ),
            _pod("silent-node", node="node02", terminal=None),
        ],
        vs_api_node=VS_API_NODE,
    )

    assert target is None
    assert refusals == (
        "app-node: terminal surface 'exec', ssh required",
        "silent-node: terminal surface None, ssh required",
    )


def test_pod_without_a_published_primary_container_is_refused_with_the_contract_reason() -> None:
    target, refusals = eligible_ssh_target(
        [_pod("bare", node="node02", primary=None)],
        vs_api_node=VS_API_NODE,
    )

    assert target is None
    assert refusals == (
        f"bare: bare: pod 'bare' carries no {PRIMARY_CONTAINER_ANNOTATION} annotation",
    )


def test_deleting_pod_and_pod_without_an_ip_are_refused() -> None:
    target, refusals = eligible_ssh_target(
        [_pod("going", node="node02", deleting=True), _pod("pending", node="node02", pod_ip=None)],
        vs_api_node=VS_API_NODE,
    )

    assert target is None
    assert refusals == ("going: being deleted", "pending: no pod IP")
