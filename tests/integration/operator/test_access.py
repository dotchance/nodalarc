"""An operator reaches the systems of a running session, and nobody else does."""

from __future__ import annotations

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from .harness.client import Operator, Refused
from .harness.network import (
    ShownNetwork,
    link_interface,
    loopback_of,
    routing_on,
    traceroute_hops,
    watch,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def test_the_terminal_gives_the_router_cli(operator: Operator) -> None:
    ground, _, _ = watch(operator, 4.0).routed_ground_links()[0]
    with operator.terminal(ground) as terminal:
        assert terminal.banner.rstrip().endswith(f"{ground}#")
        assert "FRRouting" in terminal.run("show version")


@pytest.mark.parametrize("path", ["/ws/v1/state", "/ws/v1/terminal/any-node"])
def test_a_wrong_token_opens_no_socket(operator: Operator, path: str) -> None:
    if not operator.token:
        pytest.skip("this VS-API runs without an API key")
    with pytest.raises((InvalidStatus, ConnectionClosed)):
        with connect(operator.socket_url(path, token="wrong")) as socket:
            socket.recv(timeout=5.0)


def _two_ground_routers_in_one_domain(operator: Operator) -> tuple[ShownNetwork, str, str]:
    shown = watch(operator, 4.0)
    by_domain: dict[tuple[str, str], list[str]] = {}
    for ground, _, link in shown.routed_ground_links():
        domain = routing_on(shown.nodes[ground])[link_interface(link, ground)]
        if ground not in by_domain.setdefault(domain, []):
            by_domain[domain].append(ground)
    pairs = [grounds for grounds in by_domain.values() if len(grounds) >= 2]
    if not pairs:
        pytest.skip("this session has no two linked ground routers in one routing domain")
    return shown, pairs[0][0], pairs[0][1]


def test_trace_path_is_the_path_the_routers_take(operator: Operator) -> None:
    shown, source, destination = _two_ground_routers_in_one_domain(operator)
    names = shown.node_by_address()
    target = loopback_of(shown.nodes[destination])

    def routers_path() -> list[str | None]:
        with operator.terminal(source) as terminal:
            hops = traceroute_hops(terminal.run(f"traceroute {target}", timeout=120.0))
        return [source, *(names.get(hop, hop) if hop else None for hop in hops)]

    # The path changes as satellites move. Trace Path is compared when the routers gave the same
    # path before and after it ran.
    for _ in range(3):
        before = routers_path()
        traced = operator.trace(source, destination)
        after = routers_path()
        if before == after:
            break
    else:
        pytest.fail(
            f"the routers' path from {source} to {destination} changed during every attempt"
        )
    print(
        f"trace: routers {before}; Trace Path {traced['hops']} ({traced['state']}, {traced['rtt_ms']} ms)"
    )
    assert before[-1] == destination, (
        f"traceroute from {source} did not reach {destination}: {before}"
    )
    assert traced["state"] == "reached" and traced["hops"] == before
    assert traced["reverse_state"] == "reached" and traced["reverse_hops"][0] == destination


def test_trace_path_refuses_a_node_the_session_does_not_have(operator: Operator) -> None:
    _, source, _ = _two_ground_routers_in_one_domain(operator)
    with pytest.raises(Refused) as refusal:
        operator.trace(source, "no-such-node")
    assert refusal.value.status == 404 and "no-such-node" in refusal.value.body["message"]


def test_introspect_gives_the_routers_own_answer_and_refuses_other_commands(
    operator: Operator,
) -> None:
    _, router, _ = _two_ground_routers_in_one_domain(operator)
    with operator.terminal(router) as terminal:
        typed = terminal.run("show interface brief")
    offered = operator.introspect(router, "show interface brief")
    assert offered.split() == typed.split()

    with pytest.raises(Refused) as refusal:
        operator.introspect(router, "show startup-config")
    assert refusal.value.status == 400 and refusal.value.body["message"]
