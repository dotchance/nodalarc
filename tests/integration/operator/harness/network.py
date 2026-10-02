"""The network NodalArc shows, and the same network as its routers report it.

Two extension points live here. A new routing protocol needs one entry in NEIGHBOR_READERS.
Nothing else in the tests names a protocol.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .client import Operator, Terminal

Link = dict[str, Any]
Node = dict[str, Any]


def sim_seconds(snapshot: dict[str, Any]) -> float:
    return datetime.fromisoformat(snapshot["sim_time"]).timestamp()


def link_key(link: Link) -> tuple[str, str]:
    return (link["node_a"], link["node_b"])


def link_interface(link: Link, node_id: str) -> str:
    return link["interface_a"] if link["node_a"] == node_id else link["interface_b"]


def peer_of(link: Link, node_id: str) -> str:
    return link["node_b"] if link["node_a"] == node_id else link["node_a"]


def routing_on(node: Node) -> dict[str, tuple[str, str]]:
    """Interface name to (protocol, domain) for every interface this node runs a protocol on."""
    return {
        interface["name"]: (instance["protocol"], instance["domain_id"])
        for instance in node["routing_instances"]
        for interface in instance["interfaces"]
    }


def _isis_neighbors(printed: str) -> dict[str, str]:
    return {
        circuit["interface"]: circuit["adj"]
        for area in json.loads(printed)["areas"]
        for circuit in area["circuits"]
        if circuit.get("state") == "Up"
    }


# protocol -> (command an operator types, reader of what the router prints)
NEIGHBOR_READERS: dict[str, tuple[str, Callable[[str], dict[str, str]]]] = {
    "isis": ("show isis neighbor json", _isis_neighbors),
}


def router_neighbors(terminal: Terminal, node: Node) -> dict[str, str]:
    """Interface to neighbor name, as the router itself reports its established neighbors."""
    neighbors: dict[str, str] = {}
    for protocol in sorted({protocol for protocol, _ in routing_on(node).values()}):
        if protocol not in NEIGHBOR_READERS:
            raise AssertionError(
                f"{node['node_id']} runs {protocol}; the tests have no neighbor reader for it"
            )
        command, read = NEIGHBOR_READERS[protocol]
        neighbors.update(read(terminal.run(command)))
    return neighbors


def ping_reply_times_ms(printed: str) -> list[float]:
    return [float(value) for value in re.findall(r"time=([0-9.]+) ms", printed)]


@dataclass(frozen=True)
class ShownNetwork:
    """What NodalArc showed over a stretch of the state feed."""

    snapshots: list[dict[str, Any]]

    @property
    def latest(self) -> dict[str, Any]:
        return self.snapshots[-1]

    @property
    def nodes(self) -> dict[str, Node]:
        return {node["node_id"]: node for node in self.latest["nodes"]}

    def steady_links(self) -> dict[tuple[str, str], Link]:
        """Links shown active in every snapshot, as last shown. A link that came or went is out."""
        active = [
            {link_key(link): link for link in snapshot["links"] if link["state"] == "active"}
            for snapshot in self.snapshots
        ]
        steady = set(active[0]).intersection(*active[1:])
        return {key: active[-1][key] for key in steady}

    def changing_interfaces(self, node_id: str) -> set[str]:
        steady = self.steady_links()
        return {
            link_interface(link, node_id)
            for snapshot in self.snapshots
            for link in snapshot["links"]
            if node_id in link_key(link) and link_key(link) not in steady
        }

    def routed_peer(self, link: Link, node_id: str) -> bool:
        """Whether both ends of `link` run the same protocol in the same domain over it."""
        nodes = self.nodes
        peer = peer_of(link, node_id)
        mine = routing_on(nodes[node_id]).get(link_interface(link, node_id))
        return mine is not None and mine == routing_on(nodes[peer]).get(link_interface(link, peer))

    def routed_ground_links(self) -> list[tuple[str, str, Link]]:
        """(ground router, satellite router, link) for steady ground links both ends route over."""
        nodes = self.nodes
        found = []
        for link in self.steady_links().values():
            if link["link_type"] != "ground":
                continue
            ground, satellite = link["node_a"], link["node_b"]
            if nodes[ground]["node_type"] != "ground_station":
                ground, satellite = satellite, ground
            if self.routed_peer(link, ground):
                found.append((ground, satellite, link))
        return found

    def and_now(self, state: dict[str, Any]) -> ShownNetwork:
        return ShownNetwork([*self.snapshots, state])


@dataclass(frozen=True)
class Clock:
    """The session clock as the state feed showed it. A session's time may move in steps."""

    paused: bool
    reported_speed: float
    sim_per_wall: float | None  # None when sim time did not move while watched
    wall_seconds_per_step: float | None
    sim_time: float


def read_clock(operator: Operator, at_least: float = 10.0, at_most: float = 90.0) -> Clock:
    """Watch the feed until sim time has moved three times (and `at_least` seconds passed)."""

    def moves(messages: list[tuple[float, dict[str, Any]]]) -> list[tuple[float, float]]:
        found: list[tuple[float, float]] = []
        for arrived, message in messages:
            if "links" in message and (not found or sim_seconds(message) != found[-1][1]):
                found.append((arrived, sim_seconds(message)))
        return found[1:]  # the first snapshot is not a move

    started = time.monotonic()
    messages = operator.watch_state(
        at_most,
        until=lambda heard: len(moves(heard)) >= 3 and time.monotonic() - started >= at_least,
    )
    snapshots = [message for _, message in messages if "links" in message]
    assert snapshots, f"the state feed delivered no snapshot in {at_most} s"
    moved = moves(messages)
    assert all(later[1] > earlier[1] for earlier, later in zip(moved, moved[1:], strict=False)), (
        "sim time went backward in the state feed"
    )
    rate = period = None
    if len(moved) >= 2:
        wall = moved[-1][0] - moved[0][0]
        rate = (moved[-1][1] - moved[0][1]) / wall
        period = wall / (len(moved) - 1)
    latest = snapshots[-1]
    return Clock(
        latest["playback_paused"], latest["playback_speed"], rate, period, sim_seconds(latest)
    )


def watch(operator: Operator, seconds: float = 10.0) -> ShownNetwork:
    """Watch a ready session's state feed. Fails when no session is ready or the feed is silent."""
    state = operator.state()
    assert state["session_status"] == "ready", (
        f"no session is ready (status {state['session_status']!r}: {state['session_status_detail']!r})"
    )
    snapshots = [message for _, message in operator.watch_state(seconds) if "links" in message]
    assert len(snapshots) >= 3, (
        f"the state feed delivered {len(snapshots)} snapshots in {seconds} s"
    )
    return ShownNetwork(snapshots)
