"""An operator reaches the systems of a running session, and nobody else does."""

from __future__ import annotations

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from .harness.client import Operator
from .harness.network import watch

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


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
