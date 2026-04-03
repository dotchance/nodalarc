# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""MI main loop — Measurement & Instrumentation service.

Subscribes to NATS request/reply for convergence gate and trace requests.
Publishes adapter events, probe results, and convergence results to NATS
JetStream. Records everything to SQLite.

Run: python -m measurement.mi_main --session <path> --db <sqlite_path>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import nats
import yaml
from nodalarc.constants import LOG_FORMAT
from nodalarc.db.queries import (
    insert_adapter_event,
    insert_convergence_result,
    insert_probe_result,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.metrics import (
    ConvergenceResult,
    ProbeResult,
    TraceRequest,
    TraceResponse,
)
from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.models.session import SessionConfig
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_ADAPTER_EVENT,
    SUBJECT_CONVERGENCE_RESULT,
    SUBJECT_MI_CONVERGENCE_GATE,
    SUBJECT_MI_TRACE,
    SUBJECT_PROBE_RESULT,
    nats_url,
)
from nodalarc.platform import get_platform_config

from measurement.adapters import create_adapter
from measurement.convergence_gate import ConvergenceGate

log = logging.getLogger(__name__)


def _discover_pods(namespace: str | None = None) -> list[dict[str, str]]:
    """Discover running pods via kubectl."""
    if namespace is None:
        namespace = get_platform_config().kubernetes_namespace
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                "nodalarc.io/node-id",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"Pod discovery failed: {result.stderr}")
            return []
        data = json.loads(result.stdout)
        pods = []
        for item in data.get("items", []):
            labels = item.get("metadata", {}).get("labels", {})
            pods.append(
                {
                    "node_id": labels.get("nodalarc.io/node-id", ""),
                    "pod_name": item["metadata"]["name"],
                    "role": labels.get("nodalarc.io/role", ""),
                    "pod_ip": item.get("status", {}).get("podIP", ""),
                }
            )
        return pods
    except Exception as exc:
        log.warning(f"Pod discovery error: {exc}")
        return []


class MIService:
    """Measurement & Instrumentation main service.

    Async-first: NATS for all messaging, SQLite for persistence.
    Collector loop runs in a background thread, publishes to NATS
    via the shared connection.
    """

    def __init__(
        self,
        session: SessionConfig,
        gs_file,
        stack_config: RoutingStackConfig,
        db_path: str,
        namespace: str | None = None,
    ) -> None:
        if namespace is None:
            namespace = get_platform_config().kubernetes_namespace
        self._session = session
        self._gs_file = gs_file
        self._stack_config = stack_config
        self._db_path = db_path
        self._namespace = namespace

        # Database
        self._db_conn = sqlite3.connect(db_path, check_same_thread=False)
        create_tables(self._db_conn)
        self._db_lock = threading.Lock()

        # Protocol adapter
        self._adapter = create_adapter(stack_config.mi_adapter)

        # Flow manager (lazy init — needs pods to be running)
        self._flow_manager = None

        # Convergence gate
        self._gate = ConvergenceGate(
            convergence_config=session.convergence,
            active_flows_fn=self._get_active_flows,
            adapter=self._adapter,
        )

        self._running = False
        self._nc: nats.NATS | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_active_flows(self) -> dict:
        if self._flow_manager:
            return self._flow_manager.active_flows
        return {}

    def _start_adapters(self) -> None:
        """Discover pods and start adapters for each."""
        pods = _discover_pods(self._namespace)
        for pod in pods:
            if pod["node_id"] and pod["role"] in ("satellite", "ground_station"):
                try:
                    self._adapter.start(pod["node_id"], pod["pod_ip"])
                except Exception as exc:
                    log.warning(f"Failed to start adapter for {pod['node_id']}: {exc}")

    def _start_flow_manager(self) -> None:
        """Initialize and configure flow manager."""
        from measurement.flow_manager import FlowManager

        self._flow_manager = FlowManager(
            session=self._session,
            gs_file=self._gs_file,
            namespace=self._namespace,
        )
        try:
            self._flow_manager.load_initial_flows()
        except Exception as exc:
            log.warning(f"Failed to load initial flows: {exc}")

    def _publish_sync(self, subject: str, payload: bytes) -> None:
        """Publish to NATS from a sync thread. Fire-and-forget."""
        if self._nc is None or self._nc.is_closed or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._nc.publish(subject, payload), self._loop).result(
                timeout=5
            )
        except Exception as exc:
            log.debug(f"NATS publish failed: {exc}")

    def _collector_loop(self) -> None:
        """Periodically collect events from all adapters. Runs in background thread."""
        while self._running:
            for pod in _discover_pods(self._namespace):
                node_id = pod["node_id"]
                if not node_id:
                    continue

                try:
                    self._adapter.poll(node_id)
                except AttributeError:
                    pass
                except Exception as exc:
                    log.debug(f"Poll failed for {node_id}: {exc}")

                try:
                    events = self._adapter.get_events(node_id)
                except Exception as exc:
                    log.debug(f"get_events failed for {node_id}: {exc}")
                    continue

                for event in events:
                    with self._db_lock:
                        try:
                            insert_adapter_event(self._db_conn, event)
                        except Exception as exc:
                            log.warning(f"DB insert failed: {exc}")
                    self._publish_sync(SUBJECT_ADAPTER_EVENT, event.model_dump_json().encode())

            if self._flow_manager:
                try:
                    results = self._flow_manager.collect_results()
                    for r in results:
                        now = datetime.now(UTC)
                        probe_result = ProbeResult(
                            sim_time=now,
                            wall_time=now,
                            flow_id=r.get("flow_id", ""),
                            src_node=r.get("src_node", ""),
                            dst_node=r.get("dst_node", ""),
                            packets_sent=r.get("packets_sent", 0),
                            packets_received=r.get("packets_received", 0),
                            latency_min_ms=r.get("latency_min_ms", 0.0),
                            latency_max_ms=r.get("latency_max_ms", 0.0),
                            latency_avg_ms=r.get("latency_avg_ms", 0.0),
                            jitter_ms=r.get("jitter_ms", 0.0),
                        )
                        with self._db_lock:
                            try:
                                insert_probe_result(self._db_conn, probe_result)
                            except Exception as exc:
                                log.warning(f"DB probe insert failed: {exc}")
                        self._publish_sync(
                            SUBJECT_PROBE_RESULT,
                            probe_result.model_dump_json().encode(),
                        )
                except Exception as exc:
                    log.warning(f"Probe result collection failed: {exc}")

            time.sleep(1.0)

    async def _on_convergence_request(self, msg) -> None:
        """NATS request/reply handler for convergence gate."""
        try:
            response = self._gate.handle_request(msg.data)

            # Record to SQLite
            result = ConvergenceResult.model_validate_json(response)
            with self._db_lock:
                try:
                    insert_convergence_result(self._db_conn, result)
                except Exception as exc:
                    log.warning(f"DB convergence insert failed: {exc}")

            # Publish result event
            await self._nc.publish(SUBJECT_CONVERGENCE_RESULT, response)

            # Reply to requester
            await msg.respond(response)
        except Exception as exc:
            log.warning(f"Convergence request error: {exc}")
            await msg.respond(b'{"error": "internal error"}')

    async def _on_trace_request(self, msg) -> None:
        """NATS request/reply handler for trace requests."""
        try:
            req = TraceRequest.model_validate_json(msg.data)
            resp = self._resolve_trace(req)
            await msg.respond(resp.model_dump_json().encode())
        except Exception as exc:
            log.warning(f"Trace request error: {exc}")
            resp = TraceResponse(src_node="", dst_node="", hops=[], success=False, error=str(exc))
            await msg.respond(resp.model_dump_json().encode())

    def _resolve_trace(self, req: TraceRequest) -> TraceResponse:
        """Resolve a forwarding path trace between two nodes."""
        try:
            hops = self._adapter.trace_path(req.src_node, req.dst_node)
            return TraceResponse(
                src_node=req.src_node,
                dst_node=req.dst_node,
                hops=hops if hops else [],
                success=bool(hops),
            )
        except Exception as exc:
            return TraceResponse(
                src_node=req.src_node,
                dst_node=req.dst_node,
                hops=[],
                success=False,
                error=str(exc),
            )

    async def run_async(self) -> None:
        """Async main loop — NATS subscriptions + background collector."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        self._nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
        log.info("MI NATS connected to %s", nats_url())

        # Start adapters and flow manager
        self._start_adapters()
        self._start_flow_manager()

        # Start collector thread
        collector = threading.Thread(target=self._collector_loop, daemon=True, name="mi-collector")
        collector.start()

        # NATS request/reply subscriptions
        subs = []
        try:
            subs.append(
                await self._nc.subscribe(
                    SUBJECT_MI_CONVERGENCE_GATE, cb=self._on_convergence_request
                )
            )
            subs.append(await self._nc.subscribe(SUBJECT_MI_TRACE, cb=self._on_trace_request))
        except Exception as exc:
            log.warning(f"NATS subscription setup failed: {exc}")

        log.info(
            "MI service started — %d NATS subscriptions, collector thread running",
            len(subs),
        )

        # Wait for shutdown
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            log.info("MI service cancelled")
        finally:
            import contextlib

            self._running = False
            for sub in subs:
                with contextlib.suppress(Exception):
                    await sub.unsubscribe()
            await self._nc.close()
            self._db_conn.close()
            log.info("MI service stopped")

    def run(self) -> None:
        """Synchronous entry point — runs the async loop."""
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            log.info("MI service shutting down")
            self._running = False


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="MI service")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--platform-config", default="configs/platform.yaml")
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    # Load configs
    raw = yaml.safe_load(Path(args.session).read_text())
    session = SessionConfig.model_validate(raw)

    from nodalarc.constellation_loader import load_ground_stations

    gs_file = load_ground_stations(session.ground_stations)

    if session.routing.stack is not None:
        stack_dir = Path(session.routing.stack)
        stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
        stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])
    else:
        from nodalarc.stack_resolver import resolve_stack

        resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
        stack_config = RoutingStackConfig(
            name=f"{session.routing.protocol}-{'-'.join(session.routing.extensions) or 'plain'}",
            image=resolved.image,
            daemons=resolved.daemons or None,
            config_templates=[],
            template_variables=resolved.template_variables,
            mi_adapter=resolved.mi_adapter,
            segment_routing=resolved.segment_routing,
            ttl_propagation=resolved.ttl_propagation,
            max_compression=resolved.max_compression,
            reconfigure_command=resolved.reconfigure_command,
        )

    service = MIService(
        session=session,
        gs_file=gs_file,
        stack_config=stack_config,
        db_path=args.db,
    )
    service.run()


if __name__ == "__main__":
    main()
