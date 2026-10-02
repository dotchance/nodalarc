"""What must be true of any ready session, whatever was done to reach it.

Each function compares something NodalArc shows with what the emulated network itself does, and
returns the disagreements it found. A scenario (a switch, a seek, a repair) ends by calling
`assert_session_is_truthful`; the same checks then guard every way of reaching a running session.
"""

from __future__ import annotations

from .harness.client import Operator
from .harness.network import (
    ShownNetwork,
    link_interface,
    link_key,
    peer_of,
    ping_reply_times_ms,
    read_clock,
    router_neighbors,
    watch,
)

# A reply is late by the time FRR and the kernel spend on it, and the delay NodalArc applies
# follows the moving range in steps. The fastest of several replies is compared, with this margin.
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
    for router in routers:
        with operator.terminal(router) as terminal:
            reported[router] = router_neighbors(terminal, shown.nodes[router])

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
    """A ground router pings the satellite it is shown linked to; the round trip is twice the latency."""
    links = shown.routed_ground_links()
    if not links:
        return ["the session shows no steady ground link that both ends route over"]
    measured = 0
    disagreements = []
    for ground, satellite, link in links:
        loopback = next(
            address["address"].split("/")[0]
            for address in shown.nodes[satellite]["addresses"]
            if address["purpose"] == "router_loopback" and address["family"] == "ipv4"
        )
        with operator.terminal(ground) as terminal:
            replies = ping_reply_times_ms(terminal.run(f"ping {loopback}", interrupt_after=5.0))
        after = {link_key(now): now for now in operator.state()["links"]}.get(link_key(link))
        if after is None or after["state"] != "active":
            print(f"delay: {ground} -> {satellite}: the link ended during the ping; not measured")
            continue
        measured += 1
        if not replies:
            disagreements.append(
                f"{ground} -> {satellite} ({loopback}): NodalArc shows the link and the router "
                f"reports the neighbor; ping gets no reply"
            )
            continue
        expected = link["latency_ms"] + after["latency_ms"]
        print(
            f"delay: {ground} -> {satellite}: round trip {min(replies):.3f} ms; "
            f"shown {expected / 2:.3f} ms one way ({expected:.3f} ms round trip)"
        )
        if (
            abs(min(replies) - expected)
            > PROCESSING_ALLOWANCE_MS + MOVING_RANGE_ALLOWANCE * expected
        ):
            disagreements.append(
                f"{ground} -> {satellite}: round trip {min(replies):.3f} ms; "
                f"NodalArc shows {expected / 2:.3f} ms one way ({expected:.3f} ms round trip)"
            )
    if not measured:
        disagreements.append("every ground link ended while it was being measured")
    return disagreements


def assert_session_is_truthful(operator: Operator) -> None:
    """Every truth, on the session as it is now. Reports all disagreements together."""
    shown = watch(operator)
    disagreements = [
        *clock_runs_at_the_reported_speed(operator),
        *links_shown_are_the_routers_neighbors(operator, shown),
        *latency_shown_is_the_delay_packets_get(operator, shown),
    ]
    assert not disagreements, "NodalArc shows something the network does not do:\n" + "\n".join(
        disagreements
    )
