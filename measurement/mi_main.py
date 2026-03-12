"""MI main loop — Measurement & Instrumentation service.

Subscribes to ZMQ PUB sockets from OME and TO, runs the convergence
gate, manages protocol adapters, publishes MI events, and records
everything to SQLite.

Run: python -m measurement.mi_main --session <path> --db <sqlite_path>
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
import zmq

from nodalarc.constants import LOG_FORMAT
from nodalarc.db.queries import (
    insert_adapter_event,
    insert_convergence_result,
    insert_probe_result,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult
from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.models.session import SessionConfig
from nodalarc.models.metrics import TraceRequest, TraceResponse
from nodalarc.platform import get_platform_config
from nodalarc.zmq_channels import (
    mi_convergence_gate_bind,
    mi_events_bind,
    mi_trace_bind,
    ome_events_connect,
    to_events_connect,
    TOPIC_ADAPTER_EVENT,
    TOPIC_CONVERGENCE_RESULT,
    TOPIC_PROBE_RESULT,
    encode_message,
)
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
                "kubectl", "get", "pods", "-n", namespace,
                "-l", "nodalarc.io/node-id",
                "-o", "json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"Pod discovery failed: {result.stderr}")
            return []
        data = json.loads(result.stdout)
        pods = []
        for item in data.get("items", []):
            labels = item.get("metadata", {}).get("labels", {})
            pods.append({
                "node_id": labels.get("nodalarc.io/node-id", ""),
                "pod_name": item["metadata"]["name"],
                "role": labels.get("nodalarc.io/role", ""),
                "pod_ip": item.get("status", {}).get("podIP", ""),
            })
        return pods
    except Exception as exc:
        log.warning(f"Pod discovery error: {exc}")
        return []


class MIService:
    """Measurement & Instrumentation main service."""

    def __init__(
        self,
        session: SessionConfig,
        gs_file: GroundStationFile,
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

        # ZMQ
        self._ctx = zmq.Context()
        self._pub_sock = self._ctx.socket(zmq.PUB)
        self._pub_sock.bind(mi_events_bind())

        # Convergence gate
        self._gate = ConvergenceGate(
            convergence_config=session.convergence,
            active_flows_fn=self._get_active_flows,
            adapter=self._adapter,
        )

        self._running = False

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
                    self._adapter.start(
                        pod["node_id"], pod["pod_ip"],
                    )
                except Exception as exc:
                    log.warning(
                        f"Failed to start adapter for {pod['node_id']}: {exc}"
                    )

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

    def _collector_loop(self) -> None:
        """Periodically collect events from all adapters."""
        while self._running:
            # Poll all adapters for events
            for pod in _discover_pods(self._namespace):
                node_id = pod["node_id"]
                if not node_id:
                    continue

                # Trigger poll
                try:
                    self._adapter.poll(node_id)
                except AttributeError:
                    pass  # Not all adapters have poll()
                except Exception as exc:
                    log.debug(f"Poll failed for {node_id}: {exc}")

                # Drain events
                try:
                    events = self._adapter.get_events(node_id)
                except Exception as exc:
                    log.debug(f"get_events failed for {node_id}: {exc}")
                    continue

                for event in events:
                    # Record to SQLite
                    with self._db_lock:
                        try:
                            insert_adapter_event(self._db_conn, event)
                        except Exception as exc:
                            log.warning(f"DB insert failed: {exc}")

                    # Publish to ZMQ
                    try:
                        self._pub_sock.send(encode_message(
                            TOPIC_ADAPTER_EVENT,
                            event.model_dump_json().encode(),
                        ))
                    except Exception as exc:
                        log.debug(f"ZMQ publish failed: {exc}")

            # Collect probe results
            if self._flow_manager:
                try:
                    results = self._flow_manager.collect_results()
                    for r in results:
                        now = datetime.now(timezone.utc)
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
                        self._pub_sock.send(encode_message(
                            TOPIC_PROBE_RESULT,
                            probe_result.model_dump_json().encode(),
                        ))
                except Exception as exc:
                    log.warning(f"Probe result collection failed: {exc}")

            time.sleep(1.0)

    def _convergence_loop(self) -> None:
        """Run the convergence gate on the REP socket."""
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.bind(mi_convergence_gate_bind())
        log.info(f"Convergence gate bound on {mi_convergence_gate_bind()}")

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)

        try:
            while self._running:
                socks = dict(poller.poll(timeout=1000))
                if sock in socks:
                    raw = sock.recv()
                    response = self._gate.handle_request(raw)

                    # Record convergence result
                    result = ConvergenceResult.model_validate_json(response)
                    with self._db_lock:
                        try:
                            insert_convergence_result(self._db_conn, result)
                        except Exception as exc:
                            log.warning(f"DB convergence insert failed: {exc}")

                    # Publish
                    self._pub_sock.send(encode_message(
                        TOPIC_CONVERGENCE_RESULT, response,
                    ))

                    sock.send(response)
        except Exception as exc:
            log.error(f"Convergence loop error: {exc}")
        finally:
            sock.close()
            ctx.term()

    def _trace_loop(self) -> None:
        """Run the trace REP socket — resolves forwarding paths."""
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.bind(mi_trace_bind())
        log.info(f"Trace endpoint bound on {mi_trace_bind()}")

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)

        try:
            while self._running:
                socks = dict(poller.poll(timeout=1000))
                if sock in socks:
                    raw = sock.recv()
                    try:
                        req = TraceRequest.model_validate_json(raw)
                        resp = self._resolve_trace(req)
                    except Exception as exc:
                        log.warning(f"Trace request error: {exc}")
                        resp = TraceResponse(
                            src_node=req.src_node if 'req' in dir() else "",
                            dst_node=req.dst_node if 'req' in dir() else "",
                            hops=[], success=False,
                            error=str(exc),
                        )
                    sock.send(resp.model_dump_json().encode())
        except Exception as exc:
            log.error(f"Trace loop error: {exc}")
        finally:
            sock.close()
            ctx.term()

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
                hops=[], success=False,
                error=str(exc),
            )

    def run(self) -> None:
        """Run the MI service — blocks until interrupted."""
        self._running = True
        log.info("MI service starting")

        # Start adapters
        self._start_adapters()

        # Start flow manager
        self._start_flow_manager()

        # Start collector thread
        collector = threading.Thread(
            target=self._collector_loop,
            daemon=True,
            name="mi-collector",
        )
        collector.start()

        # Start trace thread
        tracer = threading.Thread(
            target=self._trace_loop,
            daemon=True,
            name="mi-trace",
        )
        tracer.start()

        # Run convergence gate on main thread
        try:
            self._convergence_loop()
        except KeyboardInterrupt:
            log.info("MI service shutting down")
        finally:
            self._running = False
            self._pub_sock.close()
            self._ctx.term()
            self._db_conn.close()


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

    from ome.constellation_loader import load_ground_stations
    gs_file = load_ground_stations(session.ground_stations)

    stack_dir = Path(session.routing.stack)
    stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])

    service = MIService(
        session=session,
        gs_file=gs_file,
        stack_config=stack_config,
        db_path=args.db,
    )
    service.run()


if __name__ == "__main__":
    main()
