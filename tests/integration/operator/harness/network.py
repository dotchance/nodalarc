# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The network NodalArc shows, and the same network as its routers report it.

A new routing protocol needs one entry in NEIGHBOR_READERS.
Nothing else in the tests names a protocol.
"""

from __future__ import annotations

import heapq
import ipaddress
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext
from nodalarc.physics.context import tdb_seconds_of
from nodalarc.physics.satellites import (
    analytic_satellite_states,
    orbit_model_for,
    tle_satellite_states,
)

from .client import Operator, Terminal

LIGHT_KM_PER_S = 299792.458

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
    # The latest body-sample window: where each body is and how fast it turns.
    body_samples: dict[str, Any]
    # The SessionEphemeris epoch the orbits' elements are valid at.
    epoch_unix: float | None

    def angular_speed_rad_s(self, body_id: str) -> float:
        """The body's angular speed, the largest in the sample window."""
        return max(
            math.hypot(*sample["bodies"][body_id]["angular_velocity_rad_s"])
            for sample in self.body_samples["samples"]
        )

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

    def lans(self) -> dict[str, list[str]]:
        """Each LAN NodalArc shows (the IPv4 subnet of site interfaces) and the nodes on it."""
        members: dict[str, list[str]] = {}
        for node_id, node in self.nodes.items():
            for address in node["addresses"]:
                if address["purpose"] == "site_interface" and address["family"] == "ipv4":
                    lan = str(ipaddress.ip_interface(address["address"]).network)
                    members.setdefault(lan, []).append(node_id)
        return members

    def least_round_trip_ms(self, source: str, destination: str) -> float | None:
        """The least latency out plus the least latency back; None when either way has no
        path shown."""
        out = self.least_latency_ms(source, destination)
        back = self.least_latency_ms(destination, source)
        return None if out is None or back is None else out + back

    def least_latency_ms(self, source: str, destination: str) -> float | None:
        """The least total latency from one node to another over the links shown active now,
        each link taken in the direction travelled (its two directions have their own light
        time).

        Nodes on one LAN (a site, a spacecraft bus) are joined at no delay. None when the links
        shown do not connect the two nodes.
        """
        nodes = self.nodes
        reach: dict[str, dict[str, float]] = {node_id: {} for node_id in nodes}
        for link in self.latest["links"]:
            if link["state"] == "active":
                reach[link["node_a"]][link["node_b"]] = link["latency_a_to_b_ms"]
                reach[link["node_b"]][link["node_a"]] = link["latency_b_to_a_ms"]
        for together in self.lans().values():
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
        return ShownNetwork(
            [*self.snapshots, state], self.bodies, self.orbits, self.body_samples, self.epoch_unix
        )


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
    body_samples = [message for message in messages if message.get("msg_type") == "body_samples"]
    assert body_samples, "the state feed sent no body samples"
    return ShownNetwork(
        snapshots,
        ephemeris[0]["body_frames"],
        ephemeris[0]["nodes"],
        body_samples[-1],
        ephemeris[0]["epoch_unix"],
    )


class ReferencePositions:
    """Nodes' positions at a stated sim time, independent of the snapshots captured.

    Positions are on Earth-centred ICRF axes, so distances between nodes of different bodies
    are real distances. Satellites come from the session ephemeris through the shared physics
    module (the same evaluation OME and VS-API run); a ground node keeps the body-fixed position
    the feed shows for it, turned by its body's orientation at that time. A node with neither
    raises LookupError naming why.
    """

    def __init__(self, shown: ShownNetwork) -> None:
        if shown.epoch_unix is None:
            raise LookupError("the feed's session ephemeris carries no epoch")
        self._shown = shown
        self._epoch_unix = shown.epoch_unix
        self._physics = PhysicsContext(sorted(shown.bodies))
        self._frames = {
            body_id: BodyFrame(
                name=body_id,
                mean_radius_km=frame["mean_radius_km"],
                equatorial_radius_km=frame["equatorial_radius_km"],
                polar_radius_km=frame["polar_radius_km"],
                gravitational_parameter_km3_s2=frame["gravitational_parameter_km3_s2"],
                j2=frame["j2"],
            )
            for body_id, frame in shown.bodies.items()
        }

    def barycentric_at(self, node: Node, t: float) -> np.ndarray:
        """The node's position relative to the solar system barycenter, ICRF axes, at sim time
        t: its body's barycentric position (read from the ephemeris directly) plus its position
        relative to that body."""
        body_id = node["reference_body"]
        origin = np.array(self._physics.body_state(body_id, t).position_km)
        body, _velocity = self._physics.barycentric_state(body_id, tdb_seconds_of(t))
        return body + (self.at(node, t) - origin)

    def light_time_ms(self, transmitter: Node, receiver: Node, t: float) -> float:
        """One-way light time of a signal the transmitter sends at sim time t: the receiver's
        state at arrival, iterated in the barycentric frame."""
        origin = self.barycentric_at(transmitter, t)
        tau = float(np.linalg.norm(self.barycentric_at(receiver, t) - origin)) / LIGHT_KM_PER_S
        for _ in range(10):
            arrival = self.barycentric_at(receiver, t + tau)
            tau = float(np.linalg.norm(arrival - origin)) / LIGHT_KM_PER_S
        return tau * 1000.0

    def at(self, node: Node, t: float) -> np.ndarray:
        body_id = node["reference_body"]
        frame = self._frames.get(body_id)
        if frame is None:
            raise LookupError(f"{node['node_id']}: the ephemeris has no body frame for {body_id}")
        origin = np.array(self._physics.body_state(body_id, t).position_km)
        if node["node_type"] != "satellite":
            fixed = np.array(position_km(node, self._shown.bodies))
            rotation = self._physics.orientation(body_id, t).rotation
            return origin + rotation.T @ fixed
        orbit = self._shown.orbits.get(node["node_id"])
        if orbit is None:
            raise LookupError(f"{node['node_id']}: the session ephemeris has no orbit for it")
        if orbit["type"] == "keplerian":
            states = analytic_satellite_states(
                self._physics,
                body_frame=frame,
                model=orbit_model_for(orbit["propagator"]),
                elements=[
                    OrbitalElements(
                        semi_major_axis_km=orbit["semi_major_axis_km"],
                        inclination_rad=math.radians(orbit["inclination_deg"]),
                        raan_rad=math.radians(orbit["raan_deg"]),
                        mean_anomaly_rad=math.radians(orbit["mean_anomaly_deg"]),
                        eccentricity=orbit["eccentricity"],
                        argument_of_perigee_rad=math.radians(orbit["argument_of_perigee_deg"]),
                    )
                ],
                phases_s=[t - self._epoch_unix],
                master_sim_time_unix=t,
            )
        elif orbit["type"] == "tle":
            states = tle_satellite_states(
                self._physics,
                body_frame=frame,
                tles=[(orbit["tle_line_1"], orbit["tle_line_2"])],
                master_sim_time_unix=t,
            )
        else:
            raise LookupError(f"{node['node_id']}: orbit type {orbit['type']!r} has no evaluation")
        return origin + states.position_icrf_km[0]


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
