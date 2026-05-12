# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scheduler entry point.

Loads session config, builds interface/bandwidth maps, discovers pod
locations, initializes agent pool, and runs the async dispatch loop.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import json
import logging
import os
import time as _time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from nodal.logging import configure as _configure_logging
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.ground_terminals import station_ground_terminal_capacity
from nodalarc.link_metadata import build_link_metadata_maps
from nodalarc.models.addressing import (
    AddressingScheme,
)
from nodalarc.models.session import SessionConfig
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.wiring_status import parse_status_configmap

from scheduler.agent_pool import AgentPool
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


def _build_interface_map(
    session: SessionConfig,
    addressing: AddressingScheme,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], float]]:
    """Build shared interface and bandwidth maps from physical terminal config."""
    metadata = build_link_metadata_maps(session, addressing)
    return metadata.interface_map, metadata.bandwidth_map


def wait_for_wiring_gate(
    *,
    k8s_v1: Any,
    namespace: str,
    expected_nodes: set[str],
    session_id: str,
    wiring_generation: str,
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    monotonic: Callable[[], float] = _time.monotonic,
    sleep: Callable[[float], None] = _time.sleep,
) -> None:
    """Block Scheduler startup until Node Agent wiring is complete.

    Dispatching before every namespace has its veth/bridge wiring creates a
    topology that can never match OME's authoritative link state. Timeout is a
    hard failure so Kubernetes restarts the Scheduler instead of letting it
    apply links to a partially wired substrate.
    """
    expected_count = len(expected_nodes)
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        try:
            cm = k8s_v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
            _status_session, _status_generation, statuses = parse_status_configmap(cm.data)
            ready = {
                node_id
                for node_id, status in statuses.items()
                if status.session_id == session_id
                and status.wiring_generation == wiring_generation
                and status.status == "ready"
                and not status.dirty_kernel
                and all(phase.status == "ready" for phase in status.phases)
            }
            if expected_nodes.issubset(ready):
                log.info("Wiring gate passed: %d/%d nodes ready", len(ready), expected_count)
                return
            if int(monotonic()) % 10 < 2:
                log.debug("Wiring in progress: %d/%d", len(ready), expected_count)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status != 404:
                log.warning("Wiring status check error: %s", exc)
        sleep(poll_s)

    try:
        cm = k8s_v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
        _status_session, _status_generation, statuses = parse_status_configmap(cm.data)
        wired = {
            node_id
            for node_id, status in statuses.items()
            if status.session_id == session_id
            and status.wiring_generation == wiring_generation
            and status.status == "ready"
            and not status.dirty_kernel
            and all(phase.status == "ready" for phase in status.phases)
        }
    except Exception as exc:
        log.warning("Failed to read wiring status after timeout: %s", exc)
        wired = set()
    missing = sorted(expected_nodes - wired)
    log.error(
        "Wiring gate TIMEOUT after %.0fs: %d/%d wired, %d missing: %s",
        timeout_s,
        len(wired),
        expected_count,
        len(missing),
        ", ".join(missing[:20])
        + (f" ... and {len(missing) - 20} more" if len(missing) > 20 else ""),
    )
    raise RuntimeError(
        f"Wiring gate timeout: {len(wired)}/{expected_count} nodes wired; missing={missing[:20]}"
    )


def read_wiring_manifest_identity(k8s_v1: Any, namespace: str) -> WiringManifest:
    cm = k8s_v1.read_namespaced_config_map("nodalarc-topology-wiring", namespace)
    if not cm.data:
        raise RuntimeError("nodalarc-topology-wiring ConfigMap has no data")
    compressed = cm.data.get("manifest.json.gz.b64")
    if not compressed:
        raise RuntimeError("nodalarc-topology-wiring missing manifest.json.gz.b64")
    manifest_json = gzip.decompress(base64.b64decode(compressed)).decode()
    return WiringManifest.model_validate(json.loads(manifest_json))


def main() -> None:
    _configure_logging("nodal.arc.scheduler", nats_level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scheduler")
    parser.add_argument("--session", required=True, help="Path to session YAML")
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
    session_file = Path(args.session)
    while not session_file.is_file():
        log.debug("Waiting for session config at %s...", args.session)
        _time.sleep(5)
    data = yaml.safe_load(session_file.read_text())
    session = SessionConfig.model_validate(data)
    addressing = AddressingScheme(session.addressing)
    interface_map, bandwidth_map = _build_interface_map(session, addressing)
    log.debug("Interface map: %d link pairs", len(interface_map))
    from nodalarc.nats_channels import sanitize_session_id

    session_id = sanitize_session_id(session.session.name)

    # Pod location map — canonical node IDs from K8s labels
    # agent_port is legacy — PodLocationMap builds "host:port" strings but
    # NodeAgentClient extracts hostname and uses NATS subject, not TCP port.
    loc = PodLocationMap()
    if args.pid_map:
        loc.load_from_pid_map_file(args.pid_map, agent_port=0)
    else:
        loc.load_from_k8s_api(agent_port=0)
    log.debug("Pod locations:\n%s", loc.summary())

    # --- Wiring gate: wait for Node Agent to complete wiring ---
    # The Scheduler must NOT dispatch OME events until wiring is done.
    # Signal: nodalarc-wiring-status ConfigMap has one entry per wired node.
    # Same check the Operator uses (handlers.py:188-189).
    # K8s config already loaded by loc.load_from_k8s_api() above.
    import kubernetes.client
    from nodalarc.platform_config import get_platform_config

    k8s_v1 = kubernetes.client.CoreV1Api()
    expected_nodes = set(loc.node_ids)
    ns = get_platform_config().kubernetes_namespace
    wiring_manifest = read_wiring_manifest_identity(k8s_v1, ns)
    if wiring_manifest.session_id != session_id:
        raise RuntimeError(
            "Wiring manifest session mismatch: "
            f"manifest={wiring_manifest.session_id!r} scheduler={session_id!r}"
        )
    log.debug(
        "Wiring gate: waiting for %d nodes generation=%s",
        len(expected_nodes),
        wiring_manifest.wiring_generation,
    )
    wait_for_wiring_gate(
        k8s_v1=k8s_v1,
        namespace=ns,
        expected_nodes=expected_nodes,
        session_id=session_id,
        wiring_generation=wiring_manifest.wiring_generation,
    )

    # Agent pool
    pool = AgentPool()

    # Build capacity maps for MBB dispatch ordering
    constellation = load_constellation(session.constellation)
    satellites = expand_constellation(constellation)
    gs_file = load_ground_stations(session.ground_stations)

    gs_terminal_capacities: dict[str, int] = {}
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        gs_terminal_capacities[gs_id] = station_ground_terminal_capacity(gs_file, station)

    sat_ground_terminal_capacities: dict[str, int] = {}
    for sat in satellites:
        sat_id = addressing.sat_id(sat.plane, sat.slot)
        sat_ground_terminal_capacities[sat_id] = sat.ground_terminal_count

    mbb_dispatch = session.scheduling.ground.handover_mode == "mbb"
    log.info(
        "Ground handover: %s (protocol=%s)",
        session.scheduling.ground.handover_mode,
        session.routing.protocol,
    )

    from nodal.logging import set_session

    set_session(session_id)
    log.info(
        "Scheduler starting [build=%s, session_id=%s, link_pairs=%d, nodes=%d, mbb=%s]",
        os.environ.get("NODAL_BUILD", "dev"),
        session_id,
        len(interface_map),
        len(loc.node_ids),
        mbb_dispatch,
    )

    dispatcher = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        max_latency_age_s=session.dispatch.max_latency_age_ticks * session.time.step_seconds,
        compression_factor=session.time.compression,
        gs_terminal_capacities=gs_terminal_capacities,
        sat_ground_terminal_capacities=sat_ground_terminal_capacities,
        mbb_dispatch=mbb_dispatch,
        rtt_to_one_way_policy=session.dispatch.substrate_compensation.rtt_to_one_way,
        session_id=session_id,
        wiring_generation=wiring_manifest.wiring_generation,
    )

    try:
        asyncio.run(dispatcher.run())
    except KeyboardInterrupt:
        log.info("Scheduler interrupted")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
