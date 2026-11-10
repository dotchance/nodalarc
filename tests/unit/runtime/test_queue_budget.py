# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The long-delay queue capacity estimate on the shipped sessions and on a forked trunk terminal.

The Operator checks the placement it computes before creating pods, and the placement it
observes before publishing wiring. Only long-delay link ends are counted: their receive queues,
from the peer terminal's configured transmit rate and the link's longest delay, and their
transmit leaves. Local link queues are not part of the estimate, so a session without long-delay
links is never refused by it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

import kubernetes
import pytest
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.platform_config import get_platform_config
from nodalarc.resolve_session import load_session_resolution_from_file, resolve_session
from nodalarc.substrate.queue_budget import (
    RECEIVE_QUEUE_OVERHEAD,
    QueueMemoryRefused,
    long_delay_queue_demand,
)
from nodalarc.substrate.queue_sizing import (
    BUFFER_PACKETS,
    LONG_DELAY_TRANSMIT_LEAF_PACKETS,
    bytes_in_flight,
    shaper_burst_bytes,
)
from nodalarc_operator.session_deployer import check_long_delay_queue_capacity

from tests.catalog_session_fixtures import SHIPPED_ROOT, shipped_read_view

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
        mtu_bytes=config.veth_interface_mtu_bytes,
        bytes_per_buffer=config.queue_memory_bytes_per_buffer,
    )


def _charge(transmit_mbps: float, longest_delay_s: float) -> tuple[int, int]:
    """The payload and the counted bytes of one long-delay end, by the rule the estimate states:
    the peer's rate over the longest delay, its shaper burst and the queue's buffer frames at the
    MTU, at the overhead factor, plus the transmit leaf full."""
    config = get_platform_config()
    mtu = config.veth_interface_mtu_bytes
    payload = round(bytes_in_flight(transmit_mbps, longest_delay_s * 1000.0))
    held = payload + shaper_burst_bytes(transmit_mbps, mtu) + BUFFER_PACKETS * mtu
    leaf = LONG_DELAY_TRANSMIT_LEAF_PACKETS * config.queue_memory_bytes_per_buffer
    return payload, round(held * RECEIVE_QUEUE_OVERHEAD) + leaf


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


def test_only_the_long_delay_ends_count_each_from_its_peers_configured_rate(resolved):
    (server,) = _demand(resolved, _on(resolved, "node01")).values()
    assert {end.node_id for end in server.interfaces} == set(TRUNK)
    for end in server.interfaces:
        # The shipped trunk terminal transmits 1 Mbit/s; the delay runs to minutes.
        assert end.peer_node_id == ({*TRUNK} - {end.node_id}).pop()
        assert end.peer_terminal == "nodalarc:terminals/optical/optical-mars-trunk.yaml"
        assert end.peer_transmit_mbps == 1.0
        assert end.longest_delay_s > 185.0
        assert (end.payload_bytes, end.charged_bytes) == _charge(1.0, end.longest_delay_s)
    assert server.bytes == sum(end.charged_bytes for end in server.interfaces)


def _trunk_session(root: Path, *, transmit_mbps: float, receive_mbps: float):
    """earth-mars-hdtn-relay with both trunk ends on a user: fork of the trunk terminal whose
    rates the test chooses, as a user changes a terminal file."""

    def load(relative: str) -> dict:
        return load_configuration_yaml((SHIPPED_ROOT / relative).read_bytes())

    def write(relative: str, document: dict) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    terminal = load("terminals/optical/optical-mars-trunk.yaml")
    terminal["terminal"]["id"] = "mars-trunk-forked"
    terminal["terminal"]["bandwidth_mbps"] = {"transmit": transmit_mbps, "receive": receive_mbps}
    write("terminals/optical/mars-trunk-forked.yaml", terminal)
    forked_terminal = "user:terminals/optical/mars-trunk-forked.yaml"
    sets = {
        "mars_relays": ("space-node-sets/mars/mars-dtn-relays.yaml", "mto"),
        "earth_relay": ("space-node-sets/earth/geo/earth-deep-space-relay-1.yaml", "geo"),
    }
    raw = load("sessions/earth-mars-hdtn-relay.yaml")
    for segment_id, (set_path, member) in sets.items():
        node_set = load(set_path)
        entry = next(node for node in node_set["space_node_set"]["nodes"] if node["id"] == member)
        node_path = entry["node"].removeprefix("nodalarc:")
        node = load(node_path)
        node["node"]["id"] += "-forked"
        for mount in node["node"]["terminals"]:
            if mount["id"] == "trunk":
                mount["terminal"] = forked_terminal
        forked_node = node_path.replace(".yaml", "-forked.yaml")
        write(forked_node, node)
        entry["node"] = f"user:{forked_node}"
        node_set["space_node_set"]["id"] += "-forked"
        forked_set = set_path.replace(".yaml", "-forked.yaml")
        write(forked_set, node_set)
        next(seg for seg in raw["segments"] if seg["id"] == segment_id)["source"] = (
            f"user:{forked_set}"
        )
    view = FilesystemCatalogReadView(CatalogRoots(root=SHIPPED_ROOT, user_root=root))
    return resolve_session(raw, catalog=view)


@pytest.mark.parametrize("transmit_mbps", [2.0, 0.256, 100.0])
def test_each_receive_queue_follows_its_peers_transmit_rate_not_its_own_receive_rate(
    tmp_path, transmit_mbps
):
    # Each trunk terminal transmits at the chosen rate and receives at 15 Mbit/s: an end's
    # receive queue holds what its peer transmits, so the 15 Mbit/s receive rate plays no part.
    session = _trunk_session(tmp_path, transmit_mbps=transmit_mbps, receive_mbps=15.0)
    (server,) = _demand(session, _on(session, "node01")).values()
    assert {end.node_id for end in server.interfaces} == set(TRUNK)
    for end in server.interfaces:
        assert end.peer_terminal == "user:terminals/optical/mars-trunk-forked.yaml"
        assert end.peer_transmit_mbps == transmit_mbps
        assert (end.payload_bytes, end.charged_bytes) == _charge(transmit_mbps, end.longest_delay_s)


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
            mtu_bytes=get_platform_config().veth_interface_mtu_bytes,
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
    for node_id in TRUNK:
        peer = ({*TRUNK} - {node_id}).pop()
        assert f"{node_id}/isl" in message
        assert f"receiving from {peer}/isl" in message
    assert "nodalarc:terminals/optical/optical-mars-trunk.yaml at 1 Mbit/s" in message
    assert message.endswith(
        "The terminals' configured rates (their catalog files) and the installation setting "
        "network.queueMemory.serverShare (budget) change this result."
    )
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
