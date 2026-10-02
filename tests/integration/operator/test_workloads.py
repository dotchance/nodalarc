"""The applications a session runs carry data over the emulated network.

Each check acts from the workload's own shell, the one the page opens on a host node, and takes
the application's own report as evidence.
"""

from __future__ import annotations

import pytest

from .harness.client import Operator
from .harness.network import watch
from .harness.workloads import DtnEndpoint, QuicClient, find_workloads
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
