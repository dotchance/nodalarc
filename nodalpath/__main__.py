"""NodalPath entry point — python -m nodalpath"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from nodalarc.constants import LOG_FORMAT

from nodalpath.config import NodalPathConfig
from nodalpath.orchestrator.session_loader import load_pod_ip_map, load_session_context
from nodalpath.push.push_scheduler import PushScheduler, PushSchedulerConfig

log = logging.getLogger(__name__)


def _build_push_scheduler(
    config: NodalPathConfig,
    node_registry,
    interface_map,
    pod_ip_map: dict[str, str] | None = None,
) -> PushScheduler:
    sched_config = PushSchedulerConfig(
        namespace=config.namespace,
        timeout_seconds=config.push_timeout_seconds,
        use_incremental_diff=config.use_incremental_diff,
        dry_run=config.dry_run,
        transport=config.transport,
        grpc_port=config.grpc_port,
    )
    return PushScheduler(
        node_registry=node_registry,
        interface_map=interface_map,
        config=sched_config,
        pod_ip_map=pod_ip_map,
    )


async def _run_live(config: NodalPathConfig) -> None:
    import uvicorn
    from nodalarc.zmq_channels import nodalpath_console_port

    from nodalpath.console.server import build_app
    from nodalpath.console.state import ConsoleState
    from nodalpath.integration.live_orchestrator import LiveOrchestrator
    from nodalpath.integration.zmq_publisher import AlmanacPublisher

    node_registry, interface_map, prefix_map, bandwidth_map, static_edges = load_session_context(
        config.session_path,
    )

    # Read session name from YAML for display (not the file path)
    import yaml as _yaml

    _session_raw = _yaml.safe_load(config.session_path.read_text())
    _session_name = _session_raw.get("session", {}).get("name", str(config.session_path))

    console_state = ConsoleState(
        session_path=_session_name,
        transport=config.transport,
        dry_run=config.dry_run,
        nodes_in_registry=len(node_registry),
    )

    # Build pod_ip_map early so it's available for both push and inspection
    pod_ip_map: dict[str, str] | None = None
    if config.transport == "grpc":
        node_ids = list(node_registry.keys())
        pod_ip_map = load_pod_ip_map(node_ids, namespace=config.namespace)
        if not pod_ip_map:
            log.warning("No pod IPs resolved — push will fail for all nodes")

    push_scheduler = _build_push_scheduler(config, node_registry, interface_map, pod_ip_map)
    publisher = AlmanacPublisher(config.events_bind)

    from nodalpath.orchestrator.link_state_store import LinkStateStore

    link_state_output = (
        config.almanac_output_path.with_suffix(".links.jsonl")
        if config.almanac_output_path is not None
        else None
    )
    link_state_store = LinkStateStore(output_path=link_state_output)

    if link_state_output is not None and link_state_output.exists():
        loaded = link_state_store.load_from_jsonl(link_state_output)
        log.info("Loaded %d link state entries from %s", loaded, link_state_output)

    # Node inspection / feedback loop
    from nodalpath.integration.node_inspector import NodeInspector

    node_inspector: NodeInspector | None = None
    if config.transport == "grpc" and pod_ip_map:
        node_inspector = NodeInspector(
            pod_ip_map=pod_ip_map,
            grpc_port=config.grpc_port,
            grpc_timeout=config.push_timeout_seconds,
        )

    orchestrator = LiveOrchestrator(
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=bandwidth_map,
        static_edges=static_edges,
        push_scheduler=push_scheduler,
        publisher=publisher,
        ome_connect=config.ome_connect,
        to_connect=config.to_connect,
        console_state=console_state,
        link_state_store=link_state_store,
        node_inspector=node_inspector,
        inspection_on_push=config.inspection_on_push,
        inspection_on_link_event=config.inspection_on_link_event,
        inspection_heartbeat_interval_s=config.inspection_heartbeat_interval_s,
    )

    almanac_store = orchestrator.almanac_store

    # Load almanac history from previous run if output path is configured
    if config.almanac_output_path is not None:
        loaded = almanac_store.load_from_jsonl(config.almanac_output_path)
        log.info("Loaded %d almanac entries from %s", loaded, config.almanac_output_path)

    from nodalpath.engine.path_deriver import PathDeriver

    path_deriver = PathDeriver(
        almanac_store=almanac_store,
        prefix_map=prefix_map,
        node_registry=node_registry,
        interface_map=interface_map,
        snapshot_builder=orchestrator.snapshot_builder,
    )

    console_app = build_app(
        console_state,
        almanac_store=almanac_store,
        prefix_map=prefix_map,
        link_state_store=link_state_store,
        path_deriver=path_deriver,
        node_inspector=node_inspector,
    )
    uvicorn_config = uvicorn.Config(
        console_app,
        host="0.0.0.0",
        port=nodalpath_console_port(),
        log_level="warning",
        access_log=False,
    )
    console_server = uvicorn.Server(uvicorn_config)

    log.info(
        "NodalPath live mode starting (transport=%s, dry_run=%s, console=http://0.0.0.0:%d)",
        config.transport,
        config.dry_run,
        nodalpath_console_port(),
    )

    tasks = [orchestrator.run(), console_server.serve()]

    if config.lookahead_enabled and config.timeline_path is not None:
        from nodalpath.integration.lookahead_worker import LookaheadWorker

        lookahead = LookaheadWorker(
            timeline_path=config.timeline_path,
            node_registry=node_registry,
            interface_map=interface_map,
            prefix_map=prefix_map,
            bandwidth_map=bandwidth_map,
            almanac_store=almanac_store,
            lookahead_horizon_s=config.lookahead_horizon_s,
            console_state=console_state,
            link_state_store=link_state_store,
            static_edges=static_edges,
        )
        tasks.append(lookahead.run())
        log.info(
            "LookaheadWorker enabled (horizon=%ds, file=%s)",
            config.lookahead_horizon_s,
            config.timeline_path,
        )
    else:
        log.info("LookaheadWorker disabled (pass --timeline to enable)")

    try:
        await asyncio.gather(*tasks)
    finally:
        publisher.close()
        log.info(
            "NodalPath stopped — %d transitions processed",
            orchestrator.transition_count,
        )


async def _run_console(config: NodalPathConfig | None = None) -> None:
    """Console-only mode — serve the web UI with live path tracing.

    Used when NodalPath is started alongside a non-nodalpath-fwd session
    (IS-IS, OSPF, etc.). If a session config is provided, loads the node
    registry and wires a LivePathTracer that runs real traceroute through
    the emulated FRR pods.
    """
    import uvicorn
    import yaml
    from nodalarc.models.routing_stack import RoutingStackConfig
    from nodalarc.models.session import SessionConfig
    from nodalarc.zmq_channels import nodalpath_console_port

    from nodalpath.console.server import build_app
    from nodalpath.console.state import ConsoleState

    live_path_tracer = None
    trace_mode: str | None = None
    session_label = "(no session)"

    if config is not None and config.session_path is not None:
        _raw = yaml.safe_load(config.session_path.read_text())
        session_label = _raw.get("session", {}).get("name", str(config.session_path))
        node_registry, _iface_map, _prefix_map, _bw_map, _static_edges = load_session_context(
            config.session_path,
        )

        # Determine trace mode from routing stack config
        raw = yaml.safe_load(config.session_path.read_text())
        session = SessionConfig.model_validate(raw)
        if session.routing.stack is not None:
            stack_dir = Path(session.routing.stack)
            stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
            stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])
            segment_routing = stack_config.segment_routing
            ttl_propagation = stack_config.ttl_propagation
        else:
            from nodalarc.stack_resolver import resolve_stack

            resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
            segment_routing = resolved.segment_routing
            ttl_propagation = resolved.ttl_propagation

        if segment_routing:
            trace_mode = "sr-pipe" if ttl_propagation == "pipe" else "sr-uniform"
        else:
            trace_mode = "ip"

        from nodalpath.engine.live_path_tracer import LivePathTracer

        live_path_tracer = LivePathTracer(
            node_registry=node_registry,
            trace_mode=trace_mode,
        )
        log.info("Live path tracer wired with %d nodes (mode=%s)", len(node_registry), trace_mode)

    console_state = ConsoleState(
        session_path=session_label,
        transport="none",
        dry_run=False,
    )

    console_app = build_app(
        console_state,
        live_path_tracer=live_path_tracer,
        trace_mode=trace_mode,
    )
    uvicorn_config = uvicorn.Config(
        console_app,
        host="0.0.0.0",
        port=nodalpath_console_port(),
        log_level="warning",
        access_log=False,
    )
    console_server = uvicorn.Server(uvicorn_config)

    log.info("NodalPath console-only mode on http://0.0.0.0:%d", nodalpath_console_port())
    await console_server.serve()


def _run_batch(config: NodalPathConfig) -> None:
    """Batch mode — unchanged SlidingWindow.process() path."""
    from nodalpath.orchestrator.window import SlidingWindow

    node_registry, interface_map, prefix_map, bandwidth_map, static_edges = load_session_context(
        config.session_path,
    )
    push_scheduler = (
        _build_push_scheduler(config, node_registry, interface_map) if not config.dry_run else None
    )

    window = SlidingWindow(
        timeline_path=config.timeline_path,
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=bandwidth_map,
        output_path=config.output_path,
        push_scheduler=push_scheduler,
        static_edges=static_edges,
    )
    transitions = window.process()
    log.info("Batch processing complete: %d transitions", transitions)


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(description="NodalPath forwarding almanac controller")
    parser.add_argument("--session", help="Path to session YAML (required for live/batch)")
    parser.add_argument("--mode", choices=["live", "batch", "console"], default="live")
    parser.add_argument(
        "--timeline", help="OME timeline JSONL path (batch mode / enables lookahead)"
    )
    parser.add_argument("--output", help="Almanac JSONL output path")
    parser.add_argument(
        "--lookahead-horizon",
        type=int,
        default=5700,
        help="Lookahead horizon in seconds (default: 5700)",
    )
    parser.add_argument("--almanac-output", help="Almanac JSONL output path (enables persistence)")
    parser.add_argument("--no-lookahead", action="store_true", help="Disable lookahead worker")
    parser.add_argument("--transport", choices=["grpc", "vtysh"], default="grpc")
    parser.add_argument("--grpc-port", type=int, default=50051)
    parser.add_argument("--namespace", default="nodalarc")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--inspection-heartbeat",
        type=int,
        default=0,
        help="Inspection heartbeat interval in seconds (0=disabled)",
    )
    parser.add_argument(
        "--no-inspection-on-push",
        action="store_true",
        help="Disable automatic inspection after push",
    )
    parser.add_argument("--platform-config", default="configs/platform.yaml")
    parser.add_argument("--nodalpath-config", default="configs/nodalpath.yaml")
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    from nodalpath.platform import init_nodalpath_config

    init_platform_config(Path(args.platform_config))
    init_nodalpath_config(Path(args.nodalpath_config))

    if args.mode == "console":
        console_config = None
        if args.session is not None:
            console_config = NodalPathConfig(
                session_path=Path(args.session),
                mode="console",
                namespace=args.namespace,
            )
        asyncio.run(_run_console(console_config))
        return

    if args.session is None:
        parser.error("--session is required for live and batch modes")

    config = NodalPathConfig(
        session_path=Path(args.session),
        mode=args.mode,
        timeline_path=Path(args.timeline) if args.timeline else None,
        output_path=Path(args.output) if args.output else None,
        transport=args.transport,
        grpc_port=args.grpc_port,
        namespace=args.namespace,
        dry_run=args.dry_run,
        lookahead_enabled=not args.no_lookahead,
        lookahead_horizon_s=args.lookahead_horizon,
        almanac_output_path=Path(args.almanac_output) if args.almanac_output else None,
        inspection_heartbeat_interval_s=args.inspection_heartbeat,
        inspection_on_push=not args.no_inspection_on_push,
    )

    if config.mode == "batch":
        if config.timeline_path is None:
            parser.error("--timeline required for batch mode")
        _run_batch(config)
    else:
        asyncio.run(_run_live(config))


if __name__ == "__main__":
    main()
