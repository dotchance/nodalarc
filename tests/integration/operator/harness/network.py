"""The network NodalArc shows, and the same network as its routers report it.

A new routing protocol needs one entry in NEIGHBOR_READERS.
Nothing else in the tests names a protocol.
"""

from __future__ import annotations

import heapq
import json
import math
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


def _ospf_neighbors(printed: str) -> dict[str, str]:
    # FRR names the interface "term0:100.64.0.2" and the neighbor by its router ID.
    return {
        neighbor["ifaceName"].split(":")[0]: router_id
        for router_id, adjacencies in json.loads(printed)["neighbors"].items()
        for neighbor in adjacencies
        if neighbor.get("converged") == "Full"
    }


# protocol -> (command an operator types, reader of what the router prints)
# A reader returns interface -> neighbor, the neighbor as the router names it: a hostname, or
# an address NodalArc shows for a node.
NEIGHBOR_READERS: dict[str, tuple[str, Callable[[str], dict[str, str]]]] = {
    "isis": ("show isis neighbor json", _isis_neighbors),
    "ospf": ("show ip ospf neighbor json", _ospf_neighbors),
}


def router_neighbors(
    terminal: Terminal, node: Node, node_by_address: dict[str, str]
) -> dict[str, str]:
    """Interface to neighbor node, as the router itself reports its established neighbors."""
    neighbors: dict[str, str] = {}
    for protocol in sorted({protocol for protocol, _ in routing_on(node).values()}):
        if protocol not in NEIGHBOR_READERS:
            raise AssertionError(
                f"{node['node_id']} runs {protocol}; the tests have no neighbor reader for it"
            )
        command, read = NEIGHBOR_READERS[protocol]
        neighbors.update(
            {
                interface: node_by_address.get(neighbor, neighbor)
                for interface, neighbor in read(terminal.run(command)).items()
            }
        )
    return neighbors


def ping_reply_times_ms(printed: str) -> list[float]:
    return [float(value) for value in re.findall(r"time=([0-9.]+) ms", printed)]


def traceroute_hops(printed: str) -> list[str | None]:
    """The address that answered at each hop of `traceroute` output; None for a silent hop."""
    hops: list[str | None] = []
    for line in printed.splitlines():
        hop = re.match(r"\s*\d+\s+(?:(\d+\.\d+\.\d+\.\d+)|\*)", line)
        if hop:
            hops.append(hop.group(1))
    return hops


def loopback_of(node: Node) -> str:
    return next(
        address["address"].split("/")[0]
        for address in node["addresses"]
        if address["purpose"] == "router_loopback" and address["family"] == "ipv4"
    )


def position_km(node: Node, bodies: dict[str, Any]) -> tuple[float, float, float]:
    """The node's position in its body's fixed frame, from the latitude, longitude and altitude shown."""
    body = bodies[node["reference_body"]]
    equatorial, polar = body["equatorial_radius_km"], body["polar_radius_km"]
    e2 = 1 - (polar * polar) / (equatorial * equatorial)
    lat, lon = math.radians(node["lat_deg"]), math.radians(node["lon_deg"])
    normal = equatorial / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (
        (normal + node["alt_km"]) * math.cos(lat) * math.cos(lon),
        (normal + node["alt_km"]) * math.cos(lat) * math.sin(lon),
        (normal * (1 - e2) + node["alt_km"]) * math.sin(lat),
    )


@dataclass(frozen=True)
class ShownNetwork:
    """What NodalArc showed over a stretch of the state feed."""

    snapshots: list[dict[str, Any]]
    bodies: dict[str, Any]
    orbits: dict[str, Any]

    @property
    def latest(self) -> dict[str, Any]:
        return self.snapshots[-1]

    @property
    def nodes(self) -> dict[str, Node]:
        return {node["node_id"]: node for node in self.latest["nodes"]}

    def node_by_address(self) -> dict[str, str]:
        return {
            address["address"].split("/")[0]: node["node_id"]
            for node in self.latest["nodes"]
            for address in node.get("addresses") or []
        }

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

    def least_latency_ms(self, source: str, destination: str) -> float | None:
        """The least total latency from one node to another over the links shown active now.

        Nodes of one site or one spacecraft are joined at no delay. None when the links shown
        do not connect the two nodes.
        """
        nodes = self.nodes
        reach: dict[str, dict[str, float]] = {node_id: {} for node_id in nodes}
        for link in self.latest["links"]:
            if link["state"] == "active":
                a, b = link_key(link)
                reach[a][b] = reach[b][a] = link["latency_ms"]
        by_place: dict[str, list[str]] = {}
        for node_id, node in nodes.items():
            by_place.setdefault(node["namespace"], []).append(node_id)
        for together in by_place.values():
            for a in together:
                for b in together:
                    if a != b:
                        reach[a][b] = 0.0
        best = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            so_far, here = heapq.heappop(queue)
            if here == destination:
                return so_far
            if so_far > best[here]:
                continue
            for neighbor, latency in reach[here].items():
                if so_far + latency < best.get(neighbor, math.inf):
                    best[neighbor] = so_far + latency
                    heapq.heappush(queue, (so_far + latency, neighbor))
        return None

    def and_now(self, state: dict[str, Any]) -> ShownNetwork:
        return ShownNetwork([*self.snapshots, state], self.bodies, self.orbits)


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
    messages = [message for _, message in operator.watch_state(seconds)]
    snapshots = [message for message in messages if "links" in message]
    assert len(snapshots) >= 3, (
        f"the state feed delivered {len(snapshots)} snapshots in {seconds} s"
    )
    ephemeris = [message for message in messages if message.get("msg_type") == "session_ephemeris"]
    assert ephemeris, "the state feed sent no session ephemeris"
    return ShownNetwork(snapshots, ephemeris[0]["body_frames"], ephemeris[0]["nodes"])


def ground_links(snapshot: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        link_key(link)
        for link in snapshot["links"]
        if link["link_type"] == "ground" and link["state"] == "active"
    }


def let_the_sky_move(
    operator: Operator, *, speed: float = 30.0, changes_wanted: int = 6, longest_wait: float = 240.0
) -> tuple[list[str], str, str]:
    """Run the session fast until ground links have come and gone, then return to 1x.

    Returns the changes seen and the sim times the run started and ended at.
    """
    changes: list[str] = []

    def enough(messages: list[tuple[float, dict[str, Any]]]) -> bool:
        snapshots = [message for _, message in messages if "links" in message]
        if len(snapshots) < 2:
            return False
        before, now = ground_links(snapshots[-2]), ground_links(snapshots[-1])
        changes.extend(f"up {a} <-> {b}" for a, b in sorted(now - before))
        changes.extend(f"down {a} <-> {b}" for a, b in sorted(before - now))
        return len(changes) >= changes_wanted

    operator.playback("set_speed", factor=speed)
    try:
        heard = operator.watch_state(longest_wait, until=enough)
    finally:
        operator.playback("set_speed", factor=1.0)
    snapshots = [message for _, message in heard if "links" in message]
    return changes, snapshots[0]["sim_time"], snapshots[-1]["sim_time"]


def wait_for_handover_overlap(
    operator: Operator, *, speed: float = 10.0, longest_wait: float = 300.0
) -> tuple[str, Link, Link] | None:
    """Run fast until a ground station is shown in a make-before-break overlap, then return to 1x.

    The overlap as NodalArc shows it: the station's old link is active with teardown ticks left
    and names its successor, and the successor link is active too. Returns the station, the old
    link and the successor link, or None when no overlap began in the wait.
    """
    found: list[tuple[str, Link, Link]] = []

    def overlap_shown(messages: list[tuple[float, dict[str, Any]]]) -> bool:
        snapshot = messages[-1][1]
        if "links" not in snapshot:
            return False
        nodes = {node["node_id"]: node for node in snapshot["nodes"]}
        active = {link_key(link): link for link in snapshot["links"] if link["state"] == "active"}
        for link in active.values():
            # Enough of the overlap has to remain to ask the router at 1x.
            if link["link_type"] != "ground" or (link["teardown_remaining_ticks"] or 0) < 15:
                continue
            a, b = link["successor_pair"] or (None, None)
            successor = active.get((a, b)) or active.get((b, a))
            if successor is None:
                continue
            ground = link["node_a"]
            if nodes[ground]["node_type"] != "ground_station":
                ground = link["node_b"]
            found.append((ground, link, successor))
            return True
        return False

    operator.playback("set_speed", factor=speed)
    try:
        operator.watch_state(longest_wait, until=overlap_shown)
    finally:
        operator.playback("set_speed", factor=1.0)
    return found[0] if found else None
