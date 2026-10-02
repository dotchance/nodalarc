"""The applications a session runs carry data over the emulated network.

Each check acts from the workload's own shell, the one the page opens on a host node, and takes
the application's own report as evidence.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from .harness.client import Operator
from .harness.network import Clock, ShownNetwork, watch
from .harness.workloads import (
    DtnEndpoint,
    QuicClient,
    expect_a_bundle,
    find_workloads,
    send_a_bundle,
)
from .truths import dtn_bundles_arrive, quic_clients_download_from_their_servers

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


@pytest.fixture(scope="module")
def workloads(operator: Operator) -> tuple[list[QuicClient], list[DtnEndpoint]]:
    return find_workloads(operator, watch(operator, 6.0))


def test_a_quic_client_downloads_a_file_from_its_server(
    operator: Operator, workloads: tuple[list[QuicClient], list[DtnEndpoint]]
) -> None:
    quic_clients, _ = workloads
    if not quic_clients:
        pytest.skip("did not run: the session has no QUIC client")
    assert not quic_clients_download_from_their_servers(operator, quic_clients)


def test_a_bundle_sent_from_a_dtn_endpoint_arrives_at_another(
    operator: Operator, workloads: tuple[list[QuicClient], list[DtnEndpoint]]
) -> None:
    _, dtn_endpoints = workloads
    if len(dtn_endpoints) < 2:
        pytest.skip("did not run: the session has fewer than two DTN endpoints")
    assert not dtn_bundles_arrive(operator, dtn_endpoints)


def _run_fast_until(operator: Operator, reached, *, longest_wait: float = 300.0) -> bool:
    """Run the session at 30x until the state feed shows `reached`, then return to 1x."""
    seen: list[bool] = []

    def shown(messages: list[tuple[float, dict[str, Any]]]) -> bool:
        snapshot = messages[-1][1]
        if "links" in snapshot and reached(ShownNetwork([snapshot], {}, {})):
            seen.append(True)
        return bool(seen)

    operator.playback("set_speed", factor=30.0)
    try:
        operator.watch_state(longest_wait, until=shown)
    finally:
        operator.playback("set_speed", factor=1.0)
    return bool(seen)


def test_a_bundle_sent_while_no_path_is_shown_arrives_when_the_path_returns(
    operator: Operator, workloads: tuple[list[QuicClient], list[DtnEndpoint]], clock: Clock
) -> None:
    """Store and forward: with no path the bundle is held, and it is delivered once a path exists."""
    _, dtn_endpoints = workloads
    if len(dtn_endpoints) < 2:
        pytest.skip("did not run: the session has fewer than two DTN endpoints")
    sender, receiver = dtn_endpoints[0], dtn_endpoints[1]

    def path(network: ShownNetwork) -> float | None:
        return network.least_latency_ms(sender.node_id, receiver.node_id)

    if not _run_fast_until(operator, lambda network: path(network) is None):
        pytest.skip("did not run: NodalArc showed a path between the endpoints for 150 sim minutes")
    if path(watch(operator, 4.0)) is not None:
        pytest.skip("did not run: the path came back before a bundle could be sent")

    agent, payload = f"optest{uuid.uuid4().hex[:8]}", f"bundle-{uuid.uuid4().hex}"
    with operator.terminal(receiver.node_id) as inbox:
        expect_a_bundle(inbox, receiver, agent)
        status, printed = send_a_bundle(operator, sender, receiver, agent, payload)
        assert status == 0, f"{sender.node_id} could not send a bundle: {printed}"
        print(f"sent {payload} from {sender.eid} to {receiver.eid} with no path shown")

        assert _run_fast_until(operator, lambda network: path(network) is not None), (
            "NodalArc showed no path between the endpoints for another 150 sim minutes"
        )
        print("NodalArc shows a path again")
        inbox.wait_for(payload, 300.0)
        arrived = inbox.finish(20.0)
    assert f"Received Bundle from '{sender.eid}" in arrived, arrived
