# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The long-delay queue capacity estimate on the shipped sessions.

The Operator checks the placement it computes before creating pods, and the placement it
observes before publishing wiring. Only long-delay link ends are counted: their receive queues
and their transmit leaves. Local link queues are not part of the estimate, so a session without
long-delay links is never refused by it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

import kubernetes
import pytest
from nodalarc.platform_config import get_platform_config
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.substrate.queue_budget import QueueMemoryRefused, long_delay_queue_demand
from nodalarc.substrate.queue_sizing import (
    LONG_DELAY_TRANSMIT_LEAF_PACKETS,
    envelope_occupancy_packets,
)
from nodalarc_operator.session_deployer import check_long_delay_queue_capacity

from tests.catalog_session_fixtures import shipped_read_view

SESSIONS = Path("catalog/nodalarc/sessions")
SESSION = SESSIONS / "earth-mars-hdtn-relay.yaml"
TRUNK = ("earth-relay-geo", "mars-relays-mto")
SERVERS = ["node01", "node02", "node03"]


def _resolve(path: Path):
    return load_session_resolution_from_file(path, catalog=shipped_read_view()).resolved


@pytest.fixture(scope="module")
def resolved():
    return _resolve(SESSION)


def _demand(resolved, placement):
    config = get_platform_config()
    return long_delay_queue_demand(
        resolved,
        placement,
        envelope=config.long_delay_envelope,
        bytes_per_buffer=config.queue_memory_bytes_per_buffer,
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


def _on(resolved, server: str, **moved: str) -> dict[str, str]:
    placement = dict.fromkeys((node.node_id for node in resolved.nodes), server)
    placement.update(moved)
    return placement


def test_only_the_long_delay_ends_count_with_their_receive_queues_and_transmit_leaves(resolved):
    (server,) = _demand(resolved, _on(resolved, "node01")).values()
    assert {end.node_id for end in server.interfaces} == set(TRUNK)
    envelope = get_platform_config().long_delay_envelope
    for end in server.interfaces:
        # The trunk's delay runs to minutes; the transmit leaf, which the stop purges, counts
        # full at the limit the Node Agent gives it.
        assert end.longest_delay_s > 185.0
        assert end.packets == (
            envelope_occupancy_packets(envelope, end.longest_delay_s * 1000.0)
            + LONG_DELAY_TRANSMIT_LEAF_PACKETS
        )
    assert server.bytes == sum(end.packets for end in server.interfaces) * (
        get_platform_config().queue_memory_bytes_per_buffer
    )


@pytest.mark.parametrize(
    "path",
    [
        path
        for path in sorted(SESSIONS.glob("*.yaml"))
        if path.stem not in ("earth-mars-hdtn-relay", "earth-luna-mars-dtn")
    ],
    ids=lambda path: path.stem,
)
def test_a_shipped_session_without_long_delay_links_is_never_refused(path):
    session = _resolve(path)
    assert not session.long_delay_fixed_pairs()
    placement = _on(session, "node01")
    assert (
        long_delay_queue_demand(
            session,
            placement,
            envelope=get_platform_config().long_delay_envelope,
            bytes_per_buffer=get_platform_config().queue_memory_bytes_per_buffer,
        )
        == {}
    )
    # A server with almost no allocatable memory still takes it: local link queues are not
    # part of the estimate.
    check_long_delay_queue_capacity(_servers({"node01": "1Mi"}), session, placement)


def test_an_oversized_long_delay_session_is_refused_naming_its_ends_and_settings(resolved):
    placement = _on(resolved, "node01")
    (server,) = _demand(resolved, placement).values()
    share = get_platform_config().queue_memory_server_share
    allocatable = int(server.bytes / share) - 2**20
    with pytest.raises(QueueMemoryRefused) as refused:
        check_long_delay_queue_capacity(_servers({"node01": str(allocatable)}), resolved, placement)
    message = str(refused.value)
    assert message.startswith(
        "long-delay queue capacity estimate: server node01 needs an estimated"
    )
    assert f"{TRUNK[0]}/" in message and f"{TRUNK[1]}/" in message
    assert message.endswith("network.queueMemory.serverShare (budget) change this result.")
    check_long_delay_queue_capacity(
        _servers({"node01": str(allocatable + 2 * 2**20)}), resolved, placement
    )


def test_the_observed_placement_is_judged_where_the_pods_are(resolved):
    # Computed with the trunk's ends on two servers; observed after one end's pod was recreated
    # on the other's server.
    computed = _on(resolved, "node03", **{TRUNK[0]: "node01", TRUNK[1]: "node02"})
    observed = _on(resolved, "node03", **{TRUNK[0]: "node01", TRUNK[1]: "node01"})
    computed_peak = max(server.bytes for server in _demand(resolved, computed).values())
    observed_peak = _demand(resolved, observed)["node01"].bytes
    assert observed_peak == 2 * computed_peak
    share = get_platform_config().queue_memory_server_share
    v1 = _servers(dict.fromkeys(SERVERS, str(int(1.5 * computed_peak / share))))
    check_long_delay_queue_capacity(v1, resolved, computed)
    with pytest.raises(QueueMemoryRefused, match="server node01 needs an estimated"):
        check_long_delay_queue_capacity(v1, resolved, observed)


def test_a_server_that_reports_no_allocatable_memory_is_refused(resolved):
    v1 = _servers({"node01": "32Gi", "node02": None})
    with pytest.raises(QueueMemoryRefused, match="server node02 reports no allocatable memory"):
        check_long_delay_queue_capacity(v1, resolved, _on(resolved, "node02"))
