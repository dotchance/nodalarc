"""NodalPath entry point — python -m nodalpath"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from nodalarc.constants import LOG_FORMAT
from nodalpath.config import NodalPathConfig
from nodalpath.orchestrator.session_loader import load_pod_ip_map, load_session_context
from nodalpath.push.push_scheduler import PushScheduler, PushSchedulerConfig

log = logging.getLogger(__name__)


def _build_push_scheduler(config: NodalPathConfig, node_registry, interface_map) -> PushScheduler:
    sched_config = PushSchedulerConfig(
        namespace=config.namespace,
        timeout_seconds=config.push_timeout_seconds,
        use_incremental_diff=config.use_incremental_diff,
        dry_run=config.dry_run,
        transport=config.transport,
        grpc_port=config.grpc_port,
    )
    pod_ip_map = None
    if config.transport == "grpc":
        node_ids = list(node_registry.keys())
        pod_ip_map = load_pod_ip_map(node_ids, namespace=config.namespace)
        if not pod_ip_map:
            log.warning("No pod IPs resolved — push will fail for all nodes")
    return PushScheduler(
        node_registry=node_registry,
        interface_map=interface_map,
        config=sched_config,
        pod_ip_map=pod_ip_map,
    )


async def _run_live(config: NodalPathConfig) -> None:
    import uvicorn
    from nodalpath.console.server import build_app
    from nodalpath.console.state import ConsoleState
    from nodalpath.integration.live_orchestrator import LiveOrchestrator
    from nodalpath.integration.zmq_publisher import AlmanacPublisher
    from nodalarc.zmq_channels import NODALPATH_CONSOLE_PORT

    node_registry, interface_map, prefix_map, bandwidth_map = load_session_context(
        config.session_path,
    )

    console_state = ConsoleState(
        session_path=str(config.session_path),
        transport=config.transport,
        dry_run=config.dry_run,
        nodes_in_registry=len(node_registry),
    )

    push_scheduler = _build_push_scheduler(config, node_registry, interface_map)
    publisher = AlmanacPublisher(config.events_bind)

    orchestrator = LiveOrchestrator(
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=bandwidth_map,
        push_scheduler=push_scheduler,
        publisher=publisher,
        ome_connect=config.ome_connect,
        to_connect=config.to_connect,
        console_state=console_state,
        link_state_store=link_state_store,
    )

    almanac_store = orchestrator.almanac_store

    # Load almanac history from previous run if output path is configured
    if config.almanac_output_path is not None:
        loaded = almanac_store.load_from_jsonl(config.almanac_output_path)
        log.info("Loaded %d almanac entries from %s", loaded, config.almanac_output_path)

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

    from nodalpath.engine.path_deriver import PathDeriver

    path_deriver = PathDeriver(
        almanac_store=almanac_store,
        prefix_map=prefix_map,
        node_registry=node_registry,
        interface_map=interface_map,
    )

    console_app = build_app(
        console_state,
        almanac_store=almanac_store,
        prefix_map=prefix_map,
        link_state_store=link_state_store,
        path_deriver=path_deriver,
    )
    uvicorn_config = uvicorn.Config(
        console_app,
        host="0.0.0.0",
        port=NODALPATH_CONSOLE_PORT,
        log_level="warning",
        access_log=False,
    )
    console_server = uvicorn.Server(uvicorn_config)

    log.info(
        "NodalPath live mode starting (transport=%s, dry_run=%s, console=http://0.0.0.0:%d)",
        config.transport, config.dry_run, NODALPATH_CONSOLE_PORT,
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
        )
        tasks.append(lookahead.run())
        log.info(
            "LookaheadWorker enabled (horizon=%ds, file=%s)",
            config.lookahead_horizon_s, config.timeline_path,
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


async def _run_console() -> None:
    """Console-only mode — serve the web UI without ZMQ or session context.

    Used when NodalPath is started alongside a non-nodalpath-fwd session.
    The console is accessible and ready; it just shows idle state until
    a live session connects.
    """
    import uvicorn
    from nodalpath.console.server import build_app
    from nodalpath.console.state import ConsoleState
    from nodalarc.zmq_channels import NODALPATH_CONSOLE_PORT

    console_state = ConsoleState(
        session_path="(no session)",
        transport="none",
        dry_run=False,
    )

    console_app = build_app(console_state)
    uvicorn_config = uvicorn.Config(
        console_app,
        host="0.0.0.0",
        port=NODALPATH_CONSOLE_PORT,
        log_level="warning",
        access_log=False,
    )
    console_server = uvicorn.Server(uvicorn_config)

    log.info("NodalPath console-only mode on http://0.0.0.0:%d", NODALPATH_CONSOLE_PORT)
    await console_server.serve()


def _run_batch(config: NodalPathConfig) -> None:
    """Batch mode — unchanged SlidingWindow.process() path."""
    from nodalpath.orchestrator.window import SlidingWindow

    node_registry, interface_map, prefix_map, bandwidth_map = load_session_context(
        config.session_path,
    )
    push_scheduler = _build_push_scheduler(config, node_registry, interface_map) if not config.dry_run else None

    window = SlidingWindow(
        timeline_path=config.timeline_path,
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=bandwidth_map,
        output_path=config.output_path,
        push_scheduler=push_scheduler,
    )
    transitions = window.process()
    log.info("Batch processing complete: %d transitions", transitions)


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(description="NodalPath forwarding almanac controller")
    parser.add_argument("--session", help="Path to session YAML (required for live/batch)")
    parser.add_argument("--mode", choices=["live", "batch", "console"], default="live")
    parser.add_argument("--timeline", help="OME timeline JSONL path (batch mode / enables lookahead)")
    parser.add_argument("--output", help="Almanac JSONL output path")
    parser.add_argument("--lookahead-horizon", type=int, default=5700,
                        help="Lookahead horizon in seconds (default: 5700)")
    parser.add_argument("--almanac-output", help="Almanac JSONL output path (enables persistence)")
    parser.add_argument("--no-lookahead", action="store_true", help="Disable lookahead worker")
    parser.add_argument("--transport", choices=["grpc", "vtysh"], default="grpc")
    parser.add_argument("--grpc-port", type=int, default=50051)
    parser.add_argument("--namespace", default="nodalarc")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "console":
        asyncio.run(_run_console())
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
    )

    if config.mode == "batch":
        if config.timeline_path is None:
            parser.error("--timeline required for batch mode")
        _run_batch(config)
    else:
        asyncio.run(_run_live(config))


if __name__ == "__main__":
    main()
