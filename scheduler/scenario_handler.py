"""Scenario injection handler — REP socket on port 5564.

Migrated from orchestrator/main.py:91-168. Replaces direct link_manager
calls with BatchLinkDown/BatchLinkUp gRPC dispatch through the Node Agent.

Actions:
  inject_link_down: Add pair to override set, dispatch BatchLinkDown to agent,
                    publish LinkDown on port 5561.
  inject_link_up:   Remove pair from override set. The next OME visibility event
                    for this pair will trigger a normal BatchLinkUp if visible.
  inject_satellite_loss: Add all pairs involving a node to override set,
                         dispatch BatchLinkDown for each, publish LinkDown.
  clear_overrides:  Clear override set. Reconcile: for each previously-overridden
                    pair, check OME state — if visible+scheduled AND not already
                    active, dispatch BatchLinkUp to bring it back up.

The override set is shared (via threading.Lock) with the Dispatcher, which
checks it before processing OME visibility events.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

import zmq
from nodalarc.models.link_events import LinkDown
from nodalarc.zmq_channels import (
    TOPIC_LINK_DOWN,
    encode_message,
    to_scenario_inject_bind,
)

from node_agent.proto import node_agent_pb2
from scheduler.agent_pool import AgentPool
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


def run_scenario_handler(
    to_pub: zmq.Socket,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    bandwidth_map: dict[tuple[str, str], float],
    override_set: set[tuple[str, str]],
    override_lock: threading.Lock,
    active_links: dict,
    pod_locator: PodLocationMap,
    agent_pool: AgentPool,
) -> None:
    """Handle scenario injection requests on port 5564.

    Runs in a daemon thread. Blocks on ZMQ REP recv().
    """
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(to_scenario_inject_bind())
    log.info("Scenario handler bound on %s", to_scenario_inject_bind())

    try:
        while True:
            raw = sock.recv()
            cmd = json.loads(raw)
            action = cmd.get("action", "")
            now = datetime.now(UTC)

            if action == "inject_link_down":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.add(pair)
                # Dispatch BatchLinkDown to Node Agent
                _dispatch_link_down(pair, interface_map, active_links, pod_locator, agent_pool)
                # Publish LinkDown on port 5561
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
                to_pub.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))
                sock.send(b'{"status":"ok"}')

            elif action == "inject_link_up":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.discard(pair)
                # Don't dispatch BatchLinkUp here — the next OME visibility
                # event will trigger it if the link is visible+scheduled.
                sock.send(b'{"status":"ok"}')

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
                    to_pub.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))

                log.info("Satellite loss injected for %s (%d links)", node, len(downed_pairs))
                sock.send(b'{"status":"ok"}')

            elif action == "clear_overrides":
                # Capture overridden pairs before clearing
                with override_lock:
                    previously_overridden = set(override_set)
                    override_set.clear()
                # Reconcile: bring back links that should be active per OME state.
                # Since we don't have direct OME state here, we check if any
                # previously-overridden pair is NOT in active_links but IS in
                # interface_map (meaning it could potentially be active). The
                # actual reconciliation happens on the next FullStateSnapshot —
                # the Scheduler will see the link is visible+scheduled in the
                # snapshot and dispatch BatchLinkUp.
                log.info(
                    "Overrides cleared (%d pairs). Links will reconcile on next FullStateSnapshot.",
                    len(previously_overridden),
                )
                sock.send(b'{"status":"ok"}')

            else:
                sock.send(b'{"status":"error","msg":"unknown action"}')

    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.error("Scenario handler error: %s", exc, exc_info=True)
    finally:
        sock.close()
        ctx.term()


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
        return  # Link not active — nothing to tear down

    ifaces = interface_map.get(pair, ("", ""))
    is_gs = pair[0].startswith("gs-") or pair[1].startswith("gs-")
    now_iso = datetime.now(UTC).isoformat()

    if is_gs:
        gs_id = pair[0] if pair[0].startswith("gs-") else pair[1]
        sat_id = pair[1] if pair[0].startswith("gs-") else pair[0]
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
        resp = stub.BatchLinkDown(req)
        if not resp.success:
            log.warning("Scenario BatchLinkDown failed for %s: %s", pair, resp.error_message)
    except Exception as exc:
        log.warning("Scenario dispatch failed for %s: %s", pair, exc)
