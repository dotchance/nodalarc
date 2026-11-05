# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The per-server link queue memory budget, on a shipped Earth-Mars session.

The Operator checks the placement it computes before creating pods, and the placement it
observes before publishing wiring. A reconciliation that recreates some pods after the set of
servers changed computes a placement the retained pods do not follow; the check on the observed
placement is the one that describes the kernel.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

import kubernetes
import pytest
from nodalarc.platform_config import compute_pod_placement, get_platform_config
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.substrate.queue_budget import QueueMemoryRefused, session_queue_demand
from nodalarc_operator.session_deployer import _pod_inventory, check_session_queue_memory

from tests.catalog_session_fixtures import shipped_read_view

SESSION = Path("catalog/nodalarc/sessions/earth-mars-hdtn-relay.yaml")
TRUNK = ("earth-relay-geo", "mars-relays-mto")
SERVERS = ["node01", "node02", "node03"]


@pytest.fixture(scope="module")
def resolved():
    return load_session_resolution_from_file(SESSION, catalog=shipped_read_view()).resolved


def _demand(resolved, placement):
    config = get_platform_config()
    return session_queue_demand(
        resolved,
        placement,
        envelope=config.long_delay_envelope,
        bytes_per_packet=config.queue_memory_bytes_per_packet,
    )


def _servers(allocatable: dict[str, str | None]):
    v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
    v1.list_node.return_value = kubernetes.client.V1NodeList(
        items=[
            kubernetes.client.V1Node(
                metadata=kubernetes.client.V1ObjectMeta(name=name),
                status=kubernetes.client.V1NodeStatus(
                    allocatable=None if memory is None else {"memory": memory}
                ),
            )
            for name, memory in allocatable.items()
        ]
    )
    return v1


def test_the_long_delay_trunk_ends_hold_the_most_and_count_where_their_pods_are(resolved):
    everything_on_one = dict.fromkeys((node.node_id for node in resolved.nodes), "node01")
    (server,) = _demand(resolved, everything_on_one).values()
    largest = server.interfaces[:2]
    assert {end.node_id for end in largest} == set(TRUNK)
    # The trunk's delay runs to minutes: its ends hold the envelope's sustained load for that
    # long, two orders of magnitude above any local link end.
    for end in largest:
        assert end.longest_delay_s > 185.0
        assert end.packets > 100 * server.interfaces[2].packets / 2
    assert server.bytes == sum(end.packets for end in server.interfaces) * (
        get_platform_config().queue_memory_bytes_per_packet
    )


def test_a_reconciliation_after_the_server_set_changed_is_judged_where_the_pods_are(resolved):
    inventory = _pod_inventory(resolved)
    policy = {"policy": "planePerNode", "planes_per_group": 1}
    # Pods were created while only node01 accepted session pods; one ground station pod is
    # still missing when node02 and node03 join, and the reconciliation that creates it
    # computes a placement over all three.
    created_on_one = compute_pod_placement(policy, inventory, ["node01"])
    recomputed = compute_pod_placement(policy, inventory, SERVERS)
    missing = next(node.node_id for node in resolved.nodes if node.kind == "ground_station")
    observed = {**created_on_one, missing: recomputed[missing]}

    computed_peak = max(server.bytes for server in _demand(resolved, recomputed).values())
    observed_peak = _demand(resolved, observed)["node01"].bytes
    assert observed_peak > computed_peak, "the scenario needs the retained pods to concentrate"

    # A budget between the two peaks: the computed placement fits, the pods as placed do not.
    share = get_platform_config().queue_memory_server_share
    allocatable = int((computed_peak + observed_peak) / 2 / share)
    v1 = _servers(dict.fromkeys(SERVERS, str(allocatable)))
    check_session_queue_memory(v1, resolved, recomputed)
    with pytest.raises(QueueMemoryRefused) as refused:
        check_session_queue_memory(v1, resolved, observed)
    message = str(refused.value)
    assert message.startswith("server node01 would hold up to")
    assert f"{TRUNK[0]}/" in message and f"{TRUNK[1]}/" in message


def test_a_server_that_reports_no_allocatable_memory_is_refused(resolved):
    everything_on_two = dict.fromkeys((node.node_id for node in resolved.nodes), "node02")
    v1 = _servers({"node01": "32Gi", "node02": None})
    with pytest.raises(QueueMemoryRefused, match="server node02 reports no allocatable memory"):
        check_session_queue_memory(v1, resolved, everything_on_two)
