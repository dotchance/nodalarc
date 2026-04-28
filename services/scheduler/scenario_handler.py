# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scenario injection handler — NATS request/reply.

Subscribes to nodalarc.scheduler.scenario for scenario injection commands.

Actions:
  inject_link_down: Add pair to override set, dispatch BatchLinkDown to agent,
                    publish LinkDown on NATS.
  inject_link_up:   Remove pair from override set. The next OME visibility event
                    for this pair will trigger a normal BatchLinkUp if visible.
  inject_satellite_loss: Add all pairs involving a node to override set,
                         dispatch BatchLinkDown for each, publish LinkDown.
  clear_overrides:  Clear override set. Previously-overridden pairs reconcile
                    when the OME's next VisibilityEvent arrives.

The override set is shared (via threading.Lock) with the Dispatcher, which
checks it before processing OME visibility events.

Dispatch to Node Agents runs on the Dispatcher's main asyncio loop via
asyncio.run_coroutine_threadsafe(). This avoids the deadlock from calling
sync wrappers (run_until_complete) on a running loop, and ensures the
agent_pool's shared NATS connection is used from the correct thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime

import nats
from nodalarc.models.link_events import LinkDown
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_SCENARIO_INJECT,
    link_down_subject,
    nats_url,
)
from nodalarc.proto import node_agent_pb2

from scheduler.agent_pool import AgentPool
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


def run_scenario_handler(
    to_pub,  # legacy parameter, ignored
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    override_set: set[tuple[str, str]],
    override_lock: threading.Lock,
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
    main_loop: asyncio.AbstractEventLoop,
    nc_main: nats.NATS,
    gs_capacities: dict[str, int] | None = None,
    session_id: str = "default",
) -> None:
    """Handle scenario injection requests via NATS request/reply.

    Runs in a daemon thread with its own asyncio event loop for the NATS
    subscription. Dispatch to Node Agents is forwarded to the Dispatcher's
    main loop via asyncio.run_coroutine_threadsafe().

    Parameters:
        main_loop: The Dispatcher's asyncio event loop (for dispatch).
        nc_main: The Dispatcher's NATS connection (for LinkDown publish).
        gs_capacities: GS node IDs -> terminal capacity (for GS detection).
        session_id: Session ID for NATS subject scoping.
    """
    asyncio.run(
        _run_scenario_async(
            interface_map,
            bandwidth_map,
            override_set,
            override_lock,
            active_links,
            pod_locator,
            agent_pool,
            main_loop,
            nc_main,
            gs_capacities or {},
            session_id,
        )
    )


async def _run_scenario_async(
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    override_set: set[tuple[str, str]],
    override_lock: threading.Lock,
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
    main_loop: asyncio.AbstractEventLoop,
    nc_main: nats.NATS,
    gs_capacities: dict[str, int],
    session_id: str = "default",
) -> None:
    _subj_link_down = link_down_subject(session_id)
    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    log.info(
        "Scenario handler NATS connected, subject=%s (session_id=%s)",
        SUBJECT_SCENARIO_INJECT,
        session_id,
    )

    async def _handle_request(msg):
        try:
            cmd = json.loads(msg.data)
            action = cmd.get("action", "")
            now = datetime.now(UTC)

            if action == "inject_link_down":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))

                # Dispatch on the main loop (async, thread-safe)
                errors = await _dispatch_on_main_loop(
                    _inject_link_down_on_main_loop,
                    pair,
                    interface_map,
                    active_links,
                    pod_locator,
                    agent_pool,
                    override_set,
                    override_lock,
                    gs_capacities,
                    main_loop,
                )
                if errors:
                    log.warning("Scenario inject_link_down %s errors: %s", pair, errors)

                # Publish LinkDown event on the scenario handler's own NATS connection
                ifaces = interface_map.get(pair, ("", ""))
                info = active_links.get(pair)
                if info:
                    ifaces = (info.interface_a, info.interface_b)
                event = LinkDown(
                    sim_time=now,
                    wall_time=now,
                    node_a=pair[0],
                    node_b=pair[1],
                    interface_a=ifaces[0],
                    interface_b=ifaces[1],
                    reason="scenario_inject_down",
                )
                await nc.publish(_subj_link_down, event.model_dump_json().encode())
                await msg.respond(b'{"status":"ok"}')

            elif action == "inject_link_up":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.discard(pair)
                await msg.respond(b'{"status":"ok"}')

            elif action == "inject_satellite_loss":
                node = cmd["node"]
                downed_pairs = []
                with override_lock:
                    for pair in list(interface_map.keys()):
                        if node in pair:
                            override_set.add(pair)
                            downed_pairs.append(pair)

                for pair in downed_pairs:
                    errors = await _dispatch_on_main_loop(
                        _inject_link_down_on_main_loop,
                        pair,
                        interface_map,
                        active_links,
                        pod_locator,
                        agent_pool,
                        override_set,
                        override_lock,
                        gs_capacities,
                        main_loop,
                    )
                    if errors:
                        log.warning("Satellite loss dispatch %s errors: %s", pair, errors)

                    info = active_links.get(pair)
                    ifaces = interface_map.get(pair, ("", ""))
                    if info:
                        ifaces = (info.interface_a, info.interface_b)
                    event = LinkDown(
                        sim_time=now,
                        wall_time=now,
                        node_a=pair[0],
                        node_b=pair[1],
                        interface_a=ifaces[0],
                        interface_b=ifaces[1],
                        reason="satellite_loss",
                    )
                    await nc.publish(_subj_link_down, event.model_dump_json().encode())

                log.info("Satellite loss injected for %s (%d links)", node, len(downed_pairs))
                await msg.respond(b'{"status":"ok"}')

            elif action == "clear_overrides":
                with override_lock:
                    previously_overridden = set(override_set)
                    override_set.clear()
                log.info(
                    "Overrides cleared (%d pairs). Links will reconcile on next OME visibility cycle.",
                    len(previously_overridden),
                )
                await msg.respond(b'{"status":"ok"}')

            else:
                await msg.respond(b'{"status":"error","msg":"unknown action"}')

        except Exception as exc:
            log.error("Scenario handler error: %s", exc, exc_info=True)
            await msg.respond(json.dumps({"status": "error", "msg": str(exc)}).encode())

    await nc.subscribe(SUBJECT_SCENARIO_INJECT, cb=_handle_request)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.close()


async def _dispatch_on_main_loop(coro_fn, *args) -> str | None:
    """Submit a coroutine to the main loop and await its result from this thread's loop.

    Returns None on success, or an error string on failure.
    """
    # Extract main_loop from the last positional arg
    main_loop = args[-1]
    coro_args = args[:-1]

    future = asyncio.run_coroutine_threadsafe(coro_fn(*coro_args), main_loop)
    try:
        result = await asyncio.wait_for(asyncio.wrap_future(future), timeout=30)
        return result
    except TimeoutError:
        return "Dispatcher unresponsive — timed out after 30s"
    except Exception as exc:
        return f"Dispatch error: {exc}"


async def _inject_link_down_on_main_loop(
    pair: tuple[str, str],
    interface_map: dict[tuple[str, str], tuple[str, str]],
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
    override_set: set[tuple[str, str]],
    override_lock: threading.Lock,
    gs_capacities: dict[str, int],
) -> str | None:
    """Dispatch BatchLinkDown for a single pair on the main event loop.

    Called via run_coroutine_threadsafe from the scenario handler thread.
    Returns None on success, or an error string on failure.
    """
    # Add to override set (thread-safe)
    with override_lock:
        override_set.add(pair)

    info = active_links.get(pair)
    if info is None:
        return None  # Not active — override set, no dispatch needed

    errors = await _dispatch_down_single(
        pair, info, interface_map, active_links, pod_locator, agent_pool, gs_capacities
    )
    if not errors:
        active_links.pop(pair, None)
    return errors


async def _dispatch_down_single(
    pair: tuple[str, str],
    info,  # ActiveLinkInfo
    interface_map: dict[tuple[str, str], tuple[str, str]],
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
    gs_capacities: dict[str, int],
) -> str | None:
    """Build and send BatchLinkDown for one pair. Returns None on success, error string on failure.

    Mirrors dispatcher.py _send_batch_down logic for correct multi-node dispatch:
    per-interface locality, vni, remote_node_ip fields.
    """
    node_a, node_b = pair
    locality = pod_locator.link_locality(node_a, node_b)
    if locality is None:
        return f"Pod(s) not yet scheduled for {node_a}-{node_b}"

    is_gs = info.link_type == "ground" if hasattr(info, "link_type") else False
    now_iso = datetime.now(UTC).isoformat()

    # agent_addr -> list[InterfaceDown]
    agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]] = {}

    if is_gs:
        gs_id = node_a if node_a in gs_capacities else node_b
        sat_id = node_b if node_a in gs_capacities else node_a
        gs_iface = info.interface_a if node_a in gs_capacities else info.interface_b
        sat_iface = info.interface_b if node_a in gs_capacities else info.interface_a

        vni = 0
        if locality == node_agent_pb2.CROSS_NODE:
            from nodalarc.vxlan import compute_vni

            vni = compute_vni(gs_id, sat_id, gs_iface, sat_iface)

        if locality == node_agent_pb2.LOCAL:
            agent = pod_locator.agent_addr(sat_id)
            agent_ifaces.setdefault(agent, []).append(
                node_agent_pb2.InterfaceDown(
                    node_id=gs_id,
                    interface_name=gs_iface,
                    peer_node_id=sat_id,
                    peer_interface_name=sat_iface,
                    link_type=node_agent_pb2.GROUND,
                    gs_id=gs_id,
                    sat_id=sat_id,
                    locality=locality,
                    remote_node_ip="",
                    vni=vni,
                )
            )
        else:
            # CROSS_NODE: send to both agents
            for nid, agent_addr in [
                (sat_id, pod_locator.agent_addr(sat_id)),
                (gs_id, pod_locator.agent_addr(gs_id)),
            ]:
                iface = gs_iface if nid == gs_id else sat_iface
                peer_nid = sat_id if nid == gs_id else gs_id
                peer_iface = sat_iface if nid == gs_id else gs_iface
                agent_ifaces.setdefault(agent_addr, []).append(
                    node_agent_pb2.InterfaceDown(
                        node_id=nid,
                        interface_name=iface,
                        peer_node_id=peer_nid,
                        peer_interface_name=peer_iface,
                        link_type=node_agent_pb2.GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                        locality=locality,
                        remote_node_ip="",
                        vni=vni,
                    )
                )
    else:
        # ISL
        vni = 0
        if locality == node_agent_pb2.CROSS_NODE:
            from nodalarc.vxlan import compute_vni

            vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b)

        for nid, ifname, peer_nid, peer_ifname in [
            (node_a, info.interface_a, node_b, info.interface_b),
            (node_b, info.interface_b, node_a, info.interface_a),
        ]:
            agent = pod_locator.agent_addr(nid)
            agent_ifaces.setdefault(agent, []).append(
                node_agent_pb2.InterfaceDown(
                    node_id=nid,
                    interface_name=ifname,
                    link_type=node_agent_pb2.ISL,
                    locality=locality,
                    vni=vni,
                    peer_node_id=peer_nid,
                    peer_interface_name=peer_ifname,
                )
            )

    # Dispatch to all agents concurrently
    tasks = []
    agent_addrs = list(agent_ifaces.keys())
    for addr in agent_addrs:
        stub = agent_pool.get_stub(addr)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id=f"scenario-down-{now_iso}",
            target_sim_time=now_iso,
            interfaces=agent_ifaces[addr],
        )
        tasks.append(stub.async_batch_link_down(req))

    if not tasks:
        return "No agents to dispatch to"

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Check ALL agents succeeded
    errors = []
    for i, result in enumerate(results):
        addr = agent_addrs[i]
        if isinstance(result, Exception):
            errors.append(f"agent {addr}: {result}")
        elif not result.success:
            errors.append(f"agent {addr}: {result.error_message}")

    if errors:
        return "; ".join(errors)
    return None
