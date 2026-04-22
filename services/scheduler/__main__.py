# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scheduler entry point.

Loads session config, builds interface/bandwidth maps, discovers pod
locations, initializes agent pool, and runs the async dispatch loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from pathlib import Path

import yaml
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import (
    AddressingScheme,
    assign_isl_neighbors,
    neighbors_by_node,
)
from nodalarc.models.session import SessionConfig

from scheduler.agent_pool import AgentPool
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap
from scheduler.scenario_handler import run_scenario_handler

log = logging.getLogger(__name__)


def _build_interface_map(
    session: SessionConfig,
    addressing: AddressingScheme,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], float]]:
    """Build (interface_map, bandwidth_map) keyed by canonical (min, max) node-id pairs.

    interface_map[pair] = (interface_on_node_min, interface_on_node_max)
    bandwidth_map[pair] = emulated bandwidth in Mbps for that link

    Bandwidth (R-TO-003) comes from the satellite-type and ground-station
    terminal configs — NOT a hardcoded value. For ISL pairs we take
    min(a.terminal_bandwidth, b.terminal_bandwidth); for GS-satellite pairs
    we take min(gs_terminal_bandwidth, sat_ground_terminal_bandwidth).
    """
    from nodalarc.constellation_loader import (
        expand_constellation,
        ground_link_bandwidth_mbps,
        isl_link_bandwidth_mbps,
        load_constellation,
        load_ground_stations,
    )

    constellation = load_constellation(session.constellation)
    satellites = expand_constellation(constellation)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    # node_id -> (plane, slot) — needed to resolve per-satellite terminal config
    # for bandwidth lookup (supports per-plane and per-satellite overrides).
    sat_location: dict[str, tuple[int, int]] = {
        addressing.sat_id(sat.plane, sat.slot): (sat.plane, sat.slot) for sat in satellites
    }

    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}

    # Pass 1 — populate interface_map symmetrically across both endpoints.
    for node_id, assignments in by_node.items():
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair not in interface_map:
                if node_id == pair[0]:
                    interface_map[pair] = (na.interface, "")
                else:
                    interface_map[pair] = ("", na.interface)
            else:
                existing = interface_map[pair]
                if node_id == pair[0] and not existing[0]:
                    interface_map[pair] = (na.interface, existing[1])
                elif node_id == pair[1] and not existing[1]:
                    interface_map[pair] = (existing[0], na.interface)

    # Pass 2 — resolve ISL bandwidth now that both interfaces are known.
    for pair, (iface_a, iface_b) in interface_map.items():
        node_a, node_b = pair
        if not iface_a or not iface_b:
            log.warning(
                "Interface map incomplete for pair %s (%s, %s) — skipping bandwidth resolution",
                pair,
                iface_a or "<empty>",
                iface_b or "<empty>",
            )
            continue
        plane_a, slot_a = sat_location[node_a]
        plane_b, slot_b = sat_location[node_b]
        bandwidth_map[pair] = isl_link_bandwidth_mbps(
            constellation,
            plane_a,
            slot_a,
            iface_a,
            plane_b,
            slot_b,
            iface_b,
        )

    # Ground station pairs — every (GS, sat) combination is a potential link;
    # the scheduler activates pairs based on OME visibility events at runtime.
    gs_file = load_ground_stations(session.ground_stations)
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        for sat in satellites:
            sat_id = addressing.sat_id(sat.plane, sat.slot)
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("gnd0", "gnd0")
            try:
                bandwidth_map[pair] = ground_link_bandwidth_mbps(
                    constellation, gs_file, sat.plane, sat.slot, station.name
                )
            except ValueError as exc:
                # Satellite has no ground terminals, or GS has none — the link
                # isn't wireable at emulation level. Log and skip bandwidth;
                # the pair still appears in interface_map so visibility events
                # can be observed, but this link cannot be physically activated.
                log.warning(
                    "Ground link bandwidth unresolved for %s<->%s: %s",
                    gs_id,
                    sat_id,
                    exc,
                )

    return interface_map, bandwidth_map


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scheduler")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument(
        "--ome-endpoint",
        default="",
        help="Deprecated — OME events now via NATS. Ignored.",
    )
    parser.add_argument("--pid-map", help="Path to pid_map.json from na-deploy")
    parser.add_argument(
        "--platform-config",
        default="configs/platform.yaml",
        help="Path to platform configuration YAML",
    )
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    # Wait for session config to appear (Operator creates it after CRD apply)
    import time as _time

    session_file = Path(args.session)
    while not session_file.is_file():
        log.info("Waiting for session config at %s...", args.session)
        _time.sleep(5)
    data = yaml.safe_load(session_file.read_text())
    session = SessionConfig.model_validate(data)
    addressing = AddressingScheme(session.addressing)
    interface_map, bandwidth_map = _build_interface_map(session, addressing)
    log.info("Interface map: %d link pairs", len(interface_map))

    # Pod location map — canonical node IDs from K8s labels
    # agent_port is legacy — PodLocationMap builds "host:port" strings but
    # NodeAgentClient extracts hostname and uses NATS subject, not TCP port.
    loc = PodLocationMap()
    if args.pid_map:
        loc.load_from_pid_map_file(args.pid_map, agent_port=0)
    else:
        loc.load_from_k8s_api(agent_port=0)
    log.info("Pod locations:\n%s", loc.summary())

    # --- Wiring gate: wait for Node Agent to complete wiring ---
    # The Scheduler must NOT dispatch OME events until wiring is done.
    # Signal: nodalarc-wiring-status ConfigMap has one entry per wired node.
    # Same check the Operator uses (handlers.py:188-189).
    # K8s config already loaded by loc.load_from_k8s_api() above.
    import kubernetes.client
    from nodalarc.platform_config import get_platform_config

    k8s_v1 = kubernetes.client.CoreV1Api()
    expected_nodes = set(loc.node_ids)
    expected_count = len(expected_nodes)
    ns = get_platform_config().kubernetes_namespace
    log.info("Wiring gate: waiting for %d nodes", expected_count)

    wiring_deadline = _time.monotonic() + 120
    while _time.monotonic() < wiring_deadline:
        try:
            cm = k8s_v1.read_namespaced_config_map("nodalarc-wiring-status", ns)
            wired = set(cm.data.keys()) if cm.data else set()
            if len(wired) >= expected_count:
                log.info("Wiring gate passed: %d/%d nodes wired", len(wired), expected_count)
                break
            if int(_time.monotonic()) % 10 < 2:  # log every ~10s
                log.info("Wiring in progress: %d/%d", len(wired), expected_count)
        except kubernetes.client.rest.ApiException as e:
            if e.status != 404:
                log.warning("Wiring status check error: %s", e)
        _time.sleep(2)
    else:
        try:
            cm = k8s_v1.read_namespaced_config_map("nodalarc-wiring-status", ns)
            wired = set(cm.data.keys()) if cm.data else set()
        except Exception:
            wired = set()
        missing = sorted(expected_nodes - wired)
        log.error(
            "Wiring gate TIMEOUT after 120s: %d/%d wired, %d missing: %s",
            len(wired),
            expected_count,
            len(missing),
            ", ".join(missing[:20])
            + (f" ... and {len(missing) - 20} more" if len(missing) > 20 else ""),
        )

    # Agent pool
    pool = AgentPool()

    # Override set (shared between dispatcher and scenario handler)
    override_set: set[tuple[str, str]] = set()
    override_lock = threading.Lock()

    dispatcher = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=override_set,
        override_lock=override_lock,
        compression_factor=session.time.compression,
        latency_update_interval_s=session.time.latency_update_interval_seconds,
    )

    # Scenario handler — NATS request/reply.
    scenario_thread = threading.Thread(
        target=run_scenario_handler,
        args=(
            None,  # to_pub — no longer needed, Scheduler publishes on NATS
            interface_map,
            bandwidth_map,
            override_set,
            override_lock,
            dispatcher._active_links,
            loc,
            pool,
        ),
        daemon=True,
    )
    scenario_thread.start()

    try:
        asyncio.run(dispatcher.run())
    except KeyboardInterrupt:
        log.info("Scheduler interrupted")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
