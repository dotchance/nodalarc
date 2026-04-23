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
    SUBJECT_LINK_DOWN,
    SUBJECT_SCENARIO_INJECT,
    nats_url,
)
from nodalarc.proto import node_agent_pb2

from scheduler.agent_pool import AgentPool
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


def run_scenario_handler(
    to_pub,  # legacy parameter, ignored — LinkDown published on NATS
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    override_set: set[tuple[str, str]],
    override_lock: threading.Lock,
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
) -> None:
    """Handle scenario injection requests via NATS request/reply.

    Runs in a daemon thread. Blocks on NATS subscription.
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
) -> None:
    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    log.info("Scenario handler NATS connected, subject=%s", SUBJECT_SCENARIO_INJECT)

    async def _handle_request(msg):
        try:
            cmd = json.loads(msg.data)
            action = cmd.get("action", "")
            now = datetime.now(UTC)

            if action == "inject_link_down":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.add(pair)
                _dispatch_link_down(pair, interface_map, active_links, pod_locator, agent_pool)
                ifaces = interface_map.get(pair, ("", ""))
                event = LinkDown(
                    sim_time=now,
                    wall_time=now,
                    node_a=pair[0],
                    node_b=pair[1],
                    interface_a=ifaces[0],
                    interface_b=ifaces[1],
                    reason="scenario_inject_down",
                )
                await nc.publish(SUBJECT_LINK_DOWN, event.model_dump_json().encode())
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
                    _dispatch_link_down(pair, interface_map, active_links, pod_locator, agent_pool)
                    ifaces = interface_map.get(pair, ("", ""))
                    event = LinkDown(
                        sim_time=now,
                        wall_time=now,
                        node_a=pair[0],
                        node_b=pair[1],
                        interface_a=ifaces[0],
                        interface_b=ifaces[1],
                        reason="satellite_loss",
                    )
                    await nc.publish(SUBJECT_LINK_DOWN, event.model_dump_json().encode())

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


def _dispatch_link_down(
    pair: tuple[str, str],
    interface_map: dict[tuple[str, str], tuple[str, str]],
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
) -> None:
    """Dispatch BatchLinkDown for a single link pair via the Node Agent."""
    info = active_links.pop(pair, None)
    if info is None:
        return

    ifaces = interface_map.get(pair, ("", ""))
    is_gs = info.link_type == "ground" if hasattr(info, "link_type") else False
    now_iso = datetime.now(UTC).isoformat()

    if is_gs:
        # GS sorts before sat alphabetically in normalized pairs
        gs_id, sat_id = pair[0], pair[1]
        agent_addr = pod_locator.agent_addr(sat_id)
        interfaces = [
            node_agent_pb2.InterfaceDown(
                node_id=sat_id,
                interface_name="gnd0",
                link_type=node_agent_pb2.GROUND,
                gs_id=gs_id,
                sat_id=sat_id,
            )
        ]
    else:
        node_a, node_b = pair
        agent_addr = pod_locator.agent_addr(node_a)
        interfaces = [
            node_agent_pb2.InterfaceDown(
                node_id=node_a,
                interface_name=ifaces[0],
                link_type=node_agent_pb2.ISL,
            ),
            node_agent_pb2.InterfaceDown(
                node_id=node_b,
                interface_name=ifaces[1],
                link_type=node_agent_pb2.ISL,
            ),
        ]

    try:
        stub = agent_pool.get_stub(agent_addr)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id=f"scenario-down-{now_iso}",
            target_sim_time=now_iso,
            locality=node_agent_pb2.LOCAL,
            interfaces=interfaces,
        )
        resp = stub.batch_link_down(req)
        if not resp.success:
            log.warning("Scenario BatchLinkDown failed for %s: %s", pair, resp.error_message)
    except Exception as exc:
        log.warning("Scenario dispatch failed for %s: %s", pair, exc)
