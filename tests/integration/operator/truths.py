"""What must be true of any ready session, whatever was done to reach it.

Each function compares something NodalArc shows with what the emulated network itself does, and
returns the disagreements it found. A scenario (a switch, a seek, a repair) ends by calling
`assert_session_is_truthful`; the same checks then guard every way of reaching a running session.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import Any

import yaml

from .harness.client import Operator
from .harness.network import (
    ShownNetwork,
    link_interface,
    link_key,
    loopback_of,
    peer_of,
    ping_reply_times_ms,
    position_km,
    read_clock,
    router_neighbors,
    routing_on,
    sim_seconds,
    wait_for_handover_overlap,
    watch,
)
from .harness.workloads import (
    DtnEndpoint,
    QuicClient,
    expect_a_bundle,
    find_workloads,
    run_in_shell,
    send_a_bundle,
)

# A reply is late by the time FRR and the kernel spend on it, and the delay NodalArc applies
# follows the moving range in steps. The fastest of several replies is compared, with this margin.
LIGHT_KM_PER_MS = 299.792458
RANGE_ALLOWANCE_KM = 0.05
SPEED_ALLOWANCE_KM_S = 0.05
PROCESSING_ALLOWANCE_MS = 0.5
MOVING_RANGE_ALLOWANCE = 0.03


def clock_runs_at_the_reported_speed(operator: Operator) -> list[str]:
    clock = read_clock(operator)
    if clock.paused:
        return (
            [] if clock.sim_per_wall is None else ["the session reports paused and sim time moved"]
        )
    if clock.sim_per_wall is None:
        return ["the session reports playing and sim time did not move"]
    print(
        f"clock: {clock.sim_per_wall:.3f} sim seconds per wall second; "
        f"reported speed {clock.reported_speed}; a step every {clock.wall_seconds_per_step:.1f} s"
    )
    if abs(clock.sim_per_wall - clock.reported_speed) > 0.15 * clock.reported_speed:
        return [
            f"the clock runs at {clock.sim_per_wall:.2f}x and NodalArc reports {clock.reported_speed}x"
        ]
    return []


def links_shown_are_the_routers_neighbors(operator: Operator, shown: ShownNetwork) -> list[str]:
    """On each router of a ground link: every link shown is a neighbor, and no neighbor is unshown."""
    routers = sorted(
        {
            name
            for ground, satellite, _ in shown.routed_ground_links()
            for name in (ground, satellite)
        }
    )
    if not routers:
        return ["the session shows no steady ground link that both ends route over"]
    reported = {}
    names = shown.node_by_address()
    for router in routers:
        with operator.terminal(router) as terminal:
            reported[router] = router_neighbors(terminal, shown.nodes[router], names)

    # Links that came or went while the routers were being read are left out of both sides.
    shown = shown.and_now(operator.state())
    disagreements = []
    for router in routers:
        changing = shown.changing_interfaces(router)
        links = {
            link_interface(link, router): peer_of(link, router)
            for link in shown.steady_links().values()
            if router in link_key(link) and shown.routed_peer(link, router)
        }
        neighbors = {
            interface: neighbor
            for interface, neighbor in reported[router].items()
            if not interface.startswith("terr")  # the site LAN is not a link NodalArc schedules
        }
        print(f"neighbors: {router}: shown {links}; router reports {neighbors}")
        if {k: v for k, v in links.items() if k not in changing} != {
            k: v for k, v in neighbors.items() if k not in changing
        }:
            disagreements.append(
                f"{router}: NodalArc shows links {links}; the router's neighbors are {neighbors}"
            )
    return disagreements


def latency_shown_is_the_delay_packets_get(operator: Operator, shown: ShownNetwork) -> list[str]:
    """A ground router pings the satellite it is shown linked to; the round trip is twice the latency.

    The latency shown moves with the range, so it is read just before and just after each ping,
    and the fastest reply has to fall between the two readings. A reply can be late because the
    routers are busy (a session that just started); a delay that is wrong stays wrong. A late
    result is measured once more over three times as many replies before it counts.
    """
    links = shown.routed_ground_links()
    if not links:
        return ["the session shows no steady ground link that both ends route over"]

    def latency_now(key: tuple[str, str]) -> float | None:
        now = {link_key(link): link for link in operator.state()["links"]}.get(key)
        return now["latency_ms"] if now is not None and now["state"] == "active" else None

    measured = 0
    disagreements = []
    for ground, satellite, link in links:
        loopback = loopback_of(shown.nodes[satellite])
        key = link_key(link)
        with operator.terminal(ground) as terminal:

            def ping(seconds: float) -> tuple[list[float], float, float] | None:
                before = latency_now(key)
                replies = ping_reply_times_ms(
                    terminal.run(f"ping {loopback}", interrupt_after=seconds)
                )
                after = latency_now(key)
                if before is None or after is None:
                    return None
                return replies, 2 * min(before, after), 2 * max(before, after)

            result = ping(5.0)
            if result is not None and result[0]:
                replies, low, high = result
                if min(replies) > high + PROCESSING_ALLOWANCE_MS + MOVING_RANGE_ALLOWANCE * high:
                    print(
                        f"delay: {ground} -> {satellite}: round trip {min(replies):.3f} ms is "
                        f"above {high:.3f} ms shown; measuring again over more replies"
                    )
                    result = ping(15.0)
        if result is None:
            print(f"delay: {ground} -> {satellite}: the link ended during the ping; not measured")
            continue
        measured += 1
        replies, low, high = result
        if not replies:
            disagreements.append(
                f"{ground} -> {satellite} ({loopback}): NodalArc shows the link and the router "
                f"reports the neighbor; ping gets no reply"
            )
            continue
        allowance = PROCESSING_ALLOWANCE_MS + MOVING_RANGE_ALLOWANCE * high
        print(
            f"delay: {ground} -> {satellite}: round trip {min(replies):.3f} ms; "
            f"shown {low:.3f} to {high:.3f} ms round trip during the ping"
        )
        if not low - allowance <= min(replies) <= high + allowance:
            disagreements.append(
                f"{ground} -> {satellite}: round trip {min(replies):.3f} ms; "
                f"NodalArc shows {low:.3f} to {high:.3f} ms round trip during the ping"
            )
    if not measured:
        disagreements.append("every ground link ended while it was being measured")
    return disagreements


def latency_shown_is_the_light_time_between_the_positions_shown(shown: ShownNetwork) -> list[str]:
    """Each link's latency is its range at light speed, and its range is the distance between its ends.

    NodalArc refreshes a link's range every few sim seconds. The range is compared with the
    positions in the snapshot where it was refreshed.
    """
    disagreements = []
    compared = 0
    for key in shown.steady_links():
        previous = None
        for snapshot in shown.snapshots:
            link = next(link for link in snapshot["links"] if link_key(link) == key)
            if abs(link["latency_ms"] - link["range_km"] / LIGHT_KM_PER_MS) > 1e-6:
                disagreements.append(
                    f"{key}: latency {link['latency_ms']} ms is not the light time of {link['range_km']} km"
                )
                break
            refreshed = previous is not None and link["range_km"] != previous
            previous = link["range_km"]
            if not refreshed:
                continue
            nodes = {node["node_id"]: node for node in snapshot["nodes"]}
            a, b = nodes[key[0]], nodes[key[1]]
            if a["reference_body"] != b["reference_body"]:
                break  # positions are shown per body; a link between bodies is not compared
            apart = math.dist(position_km(a, shown.bodies), position_km(b, shown.bodies))
            compared += 1
            if abs(apart - link["range_km"]) > RANGE_ALLOWANCE_KM:
                disagreements.append(
                    f"{key} at {snapshot['sim_time']}: range shown {link['range_km']:.3f} km; "
                    f"the positions shown are {apart:.3f} km apart"
                )
            break
    print(f"geometry: {compared} links compared at the snapshot that refreshed their range")
    return disagreements


def satellites_move_at_the_speed_their_orbits_require(shown: ShownNetwork) -> list[str]:
    """Between two snapshots a satellite covers the distance its orbit requires in that sim time.

    The speed on an orbit of semi-major axis a at radius r is sqrt(GM (2/r - 1/a)). Positions are
    shown in the rotating frame of the body, which adds or removes up to the body's surface speed
    at that radius.
    """
    first, last = shown.snapshots[0], shown.snapshots[-1]
    elapsed = sim_seconds(last) - sim_seconds(first)
    if last["playback_paused"] or elapsed <= 0:
        return []
    before = {node["node_id"]: node for node in first["nodes"]}
    disagreements = []
    compared = 0
    for node_id, node in shown.nodes.items():
        orbit = shown.orbits.get(node_id, {})
        if orbit.get("type") != "keplerian" or node_id not in before:
            continue
        body = shown.bodies[node["reference_body"]]
        here = position_km(node, shown.bodies)
        radius = math.hypot(*here)
        orbital = math.sqrt(
            body["gravitational_parameter_km3_s2"] * (2 / radius - 1 / orbit["semi_major_axis_km"])
        )
        frame = body["rotation_rate_rad_s"] * radius
        speed = math.dist(here, position_km(before[node_id], shown.bodies)) / elapsed
        compared += 1
        if (
            not max(0.0, orbital - frame) - SPEED_ALLOWANCE_KM_S
            <= speed
            <= orbital + frame + SPEED_ALLOWANCE_KM_S
        ):
            disagreements.append(
                f"{node_id}: moved at {speed:.3f} km/s over {elapsed:.0f} sim seconds; "
                f"its orbit requires {orbital:.3f} km/s (frame rotation up to {frame:.3f})"
            )
    print(f"motion: {compared} satellites compared over {elapsed:.0f} sim seconds")
    return disagreements


def quic_clients_download_from_their_servers(
    operator: Operator, quic_clients: list[QuicClient]
) -> list[str]:
    """Each QUIC client downloads a file from the server the session gives it.

    While NodalArc shows a path between the two, the download completes. The request goes out
    and the file comes back, so it takes at least one round trip of the shortest path shown.
    While NodalArc shows no path, no file arrives.
    """
    disagreements = []
    for client in quic_clients:
        servers = watch(operator, 4.0).node_by_address()
        if client.server_address not in servers:
            disagreements.append(
                f"{client.node_id} is told its server is {client.server_address}; "
                "NodalArc shows no node with that address"
            )
            continue
        server = servers[client.server_address]
        before = watch(operator, 4.0).least_latency_ms(client.node_id, server)
        with operator.terminal(client.node_id) as terminal:
            status, printed = run_in_shell(
                terminal,
                f"cd /tmp && picoquicdemo -D -n server {client.server_address} 4433 /1000000",
                timeout=600.0,
            )
        after = watch(operator, 4.0).least_latency_ms(client.node_id, server)
        received = re.search(r"Received (\d+) bytes in ([0-9.]+) seconds", printed)
        downloaded = status == 0 and received and "Stream 0 ended after 1000000 bytes" in printed
        print(
            f"quic: {client.node_id} -> {server}: "
            f"{received.group(0) if downloaded else 'no file arrived'}; "
            f"least latency shown {before} ms before, {after} ms after"
        )
        if before is None and after is None:
            if downloaded:
                disagreements.append(
                    f"{client.node_id} downloaded from {server}; NodalArc shows no path "
                    "between them"
                )
        elif before is None or after is None:
            print("quic: the path came or went during the download; not judged")
        elif not downloaded:
            disagreements.append(
                f"NodalArc shows a path from {client.node_id} to {server}; the download did "
                f"not complete: {printed[-600:]}"
            )
        elif float(received.group(2)) < (
            least_s := 2 * min(before, after) / 1000 * (1 - MOVING_RANGE_ALLOWANCE)
        ):
            disagreements.append(
                f"{client.node_id}: the download took {received.group(2)} s; the shortest path "
                f"NodalArc shows needs {least_s:.3f} s for one round trip"
            )
    return disagreements


def dtn_bundles_arrive(operator: Operator, dtn_endpoints: list[DtnEndpoint]) -> list[str]:
    """A bundle sent from each DTN endpoint arrives at each other one.

    While NodalArc shows a path, the bundle arrives, and no sooner than the shortest path shown
    allows. With no path shown the endpoints hold the bundle; its arrival is not judged.
    """
    disagreements = []
    for sender in dtn_endpoints:
        for receiver in dtn_endpoints:
            if sender is receiver:
                continue
            agent = f"optest{uuid.uuid4().hex[:8]}"
            payload = f"bundle-{uuid.uuid4().hex}"
            least = watch(operator, 4.0).least_latency_ms(sender.node_id, receiver.node_id)
            with operator.terminal(receiver.node_id) as inbox:
                expect_a_bundle(inbox, receiver, agent)
                sent = time.monotonic()
                status, printed = send_a_bundle(operator, sender, receiver, agent, payload)
                if status != 0:
                    disagreements.append(f"{sender.node_id} could not send a bundle: {printed}")
                    continue
                try:
                    inbox.wait_for(payload, 300.0 if least is not None else 60.0)
                except AssertionError:
                    if least is None:
                        print(
                            f"dtn: {sender.eid} -> {receiver.eid}: no path shown; the bundle "
                            "is held; not judged"
                        )
                    else:
                        disagreements.append(
                            f"NodalArc shows a path from {sender.node_id} to "
                            f"{receiver.node_id}; a bundle did not arrive in 300 s"
                        )
                    continue
                took_ms = (time.monotonic() - sent) * 1000
                arrived = inbox.finish(20.0)
            print(
                f"dtn: {sender.eid} -> {receiver.eid}: arrived after {took_ms:.0f} ms; "
                f"least latency shown {least} ms"
            )
            if f"Received Bundle from '{sender.eid}" not in arrived:
                disagreements.append(
                    f"{receiver.node_id} received the payload from another sender: {arrived}"
                )
            elif least is not None and took_ms < least * (1 - MOVING_RANGE_ALLOWANCE):
                disagreements.append(
                    f"{sender.eid} -> {receiver.eid}: the bundle arrived after {took_ms:.0f} ms; "
                    f"the shortest path NodalArc shows needs {least:.0f} ms"
                )
    return disagreements


def far_sites_answer_no_sooner_than_light(operator: Operator, shown: ShownNetwork) -> list[str]:
    """Between two ground routers of one routing domain, a packet takes at least the path shown.

    One pair per routing domain: the first ground router pings a site address of the last one.
    While NodalArc shows a path between them the ping is answered, and the round trip is at least
    twice the least latency of any path shown.
    """
    by_domain: dict[tuple[str, str], list[str]] = {}
    for ground, _, link in shown.routed_ground_links():
        domain = routing_on(shown.nodes[ground])[link_interface(link, ground)]
        if ground not in by_domain.setdefault(domain, []):
            by_domain[domain].append(ground)
    disagreements = []
    for (_protocol, domain), grounds in sorted(by_domain.items()):
        source, destination = grounds[0], grounds[-1]
        targets = [
            address["address"].split("/")[0]
            for address in shown.nodes[destination]["addresses"]
            if address["purpose"] == "site_interface" and address["family"] == "ipv4"
        ]
        if source == destination or not targets:
            continue
        before = watch(operator, 4.0).least_latency_ms(source, destination)
        with operator.terminal(source) as terminal:
            replies = ping_reply_times_ms(terminal.run(f"ping {targets[0]}", interrupt_after=5.0))
        after = watch(operator, 4.0).least_latency_ms(source, destination)
        print(
            f"far: {domain}: {source} -> {destination} ({targets[0]}): "
            f"round trips {[round(reply, 1) for reply in replies]} ms; "
            f"least latency shown {before} ms before, {after} ms after"
        )
        if before is None or after is None:
            continue  # no path shown at one end of the ping: a missing reply is not judged
        if not replies:
            disagreements.append(
                f"{source} -> {destination}: NodalArc shows a path and ping gets no reply"
            )
            continue
        least = 2 * min(before, after) * (1 - MOVING_RANGE_ALLOWANCE)
        if min(replies) < least:
            disagreements.append(
                f"{source} -> {destination}: a reply came back in {min(replies):.3f} ms; the "
                f"shortest path NodalArc shows needs {least:.3f} ms for the round trip"
            )
    return disagreements


def lan_addresses_shown_answer(operator: Operator, shown: ShownNetwork) -> list[str]:
    """Every address NodalArc shows on a LAN answers its router, in each address family."""
    disagreements = []
    for lan, members in sorted(shown.lans().items()):
        routers = [name for name in members if shown.nodes[name]["routing_instances"]]
        others = [name for name in members if name not in routers[:1]]
        if not routers or not others:
            continue
        with operator.terminal(routers[0]) as terminal:
            for name in others:
                for address in shown.nodes[name]["addresses"]:
                    if address["purpose"] != "site_interface":
                        continue
                    target = address["address"].split("/")[0]
                    command = (
                        f"ping ipv6 {target}" if address["family"] == "ipv6" else f"ping {target}"
                    )
                    replies = ping_reply_times_ms(terminal.run(command, interrupt_after=3.0))
                    print(f"lan: {routers[0]} -> {name} {target}: {len(replies)} replies")
                    if not replies:
                        disagreements.append(
                            f"{lan}: NodalArc shows {target} on {name}; it does not answer "
                            f"{routers[0]}"
                        )
    return disagreements


def no_fault_is_reported(operator: Operator) -> list[str]:
    """A session whose network does what is shown reports no fault.

    This runs with the other truths. When they hold and NodalArc still shows a station faulted,
    either the fault is real and a truth missed it, or NodalArc raises a false alarm.
    """
    state = operator.state()
    faults = [
        f"{entry['gs_id']} is shown as {entry['actuation_state']} ({entry['reason_code']})"
        for scheduler in operator.get("/api/v1/ops/health")["scheduler_instances"]
        for entry in scheduler["ground_stations"]
        if entry["actuation_state"] != "clean"
    ]
    faults += [f"actuation notice: {notice}" for notice in state["actuation_notices"]]
    if state["stale"]:
        faults.append("the state shown is marked stale")
    return faults


def assert_session_is_truthful(operator: Operator) -> None:
    """Every truth, on the session as it is now. Reports all disagreements together.

    The workloads the session runs are asked to carry data last.
    """
    shown = watch(operator, 14.0)
    disagreements = [
        *clock_runs_at_the_reported_speed(operator),
        *satellites_move_at_the_speed_their_orbits_require(shown),
        *latency_shown_is_the_light_time_between_the_positions_shown(shown),
        *links_shown_are_the_routers_neighbors(operator, shown),
        *latency_shown_is_the_delay_packets_get(operator, shown),
        *far_sites_answer_no_sooner_than_light(operator, shown),
        *lan_addresses_shown_answer(operator, shown),
    ]
    quic_clients, dtn_endpoints = find_workloads(operator, shown)
    disagreements += quic_clients_download_from_their_servers(operator, quic_clients)
    disagreements += dtn_bundles_arrive(operator, dtn_endpoints)
    disagreements += no_fault_is_reported(operator)
    assert not disagreements, "NodalArc shows something the network does not do:\n" + "\n".join(
        disagreements
    )


def _declares(document: Any, key: str, value: str) -> bool:
    if isinstance(document, dict):
        return document.get(key) == value or any(
            _declares(item, key, value) for item in document.values()
        )
    if isinstance(document, list):
        return any(_declares(item, key, value) for item in document)
    return False


def make_before_break_handover_holds_both_links(operator: Operator) -> list[str] | str:
    """Make-before-break: the new link is up before the old one goes.

    Runs the session fast until NodalArc shows a station in an overlap, then asks the station's
    own router at 1x: during the overlap it holds a neighbor on the old interface and one on the
    new interface at the same moment. Returns the disagreements, or the reason the check did
    not run.
    """
    running = next(session for session in operator.sessions() if session.get("active"))
    if not _declares(yaml.safe_load(operator.session_yaml(running)), "handover_mode", "mbb"):
        return "the session declares no make-before-break station"
    overlap = wait_for_handover_overlap(operator)
    if overlap is None:
        return "the session declares make-before-break and no overlap began in 50 sim minutes"
    ground, old, successor = overlap
    old_side = (link_interface(old, ground), peer_of(old, ground))
    new_side = (link_interface(successor, ground), peer_of(successor, ground))
    print(
        f"handover: {ground} from {old_side} to {new_side}, "
        f"{old['teardown_remaining_ticks']} ticks of overlap left when seen"
    )
    with operator.terminal(ground) as terminal:
        while True:
            state = operator.state()
            still_overlapping = any(
                link_key(link) == link_key(old) and link["teardown_remaining_ticks"]
                for link in state["links"]
            )
            shown = ShownNetwork([state], {}, {})
            neighbors = router_neighbors(terminal, shown.nodes[ground], shown.node_by_address())
            print(f"handover: overlap shown: {still_overlapping}; router neighbors: {neighbors}")
            if (
                neighbors.get(old_side[0]) == old_side[1]
                and neighbors.get(new_side[0]) == new_side[1]
            ):
                return []
            if not still_overlapping:
                return [
                    f"{ground}: NodalArc showed a make-before-break overlap from {old_side} to "
                    f"{new_side}; the router never held both neighbors at once"
                ]
            time.sleep(1.0)
