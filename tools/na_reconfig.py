"""na-reconfig — manage probe flows on a running session.

Usage:
  python -m tools.na_reconfig --session <path> --add-flow test1:ground-gs-hawthorne:ground-gs-frankfurt:udp:100:continuous
  python -m tools.na_reconfig --live --remove-flow test1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from nodal.logging import configure as _configure_logging
from nodalarc.prepared_tree import load_prepared_tree_session_resolution
from nodalarc.resolve_session import SessionResolution

log = logging.getLogger(__name__)


def load_live_session_resolution(
    *,
    namespace: str = "nodalarc",
    installed_shipped_root: str | Path = "catalog/nodalarc",
) -> SessionResolution:
    """Load the selected CR root and exact uploaded catalog closure."""
    import kubernetes.client
    import kubernetes.config
    from nodalarc.cr_runtime_config import (
        CR_GROUP,
        CR_NAME,
        CR_PLURAL,
        CR_VERSION,
        ConstellationSpecStatus,
        load_cr_runtime_config,
    )

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    custom_objects = kubernetes.client.CustomObjectsApi()
    core_v1 = kubernetes.client.CoreV1Api()
    cr = custom_objects.get_namespaced_custom_object(
        group=CR_GROUP,
        version=CR_VERSION,
        namespace=namespace,
        plural=CR_PLURAL,
        name=CR_NAME,
    )
    run_id = ConstellationSpecStatus.from_cr(cr.get("status")).session_run_id or ""
    if not run_id:
        raise RuntimeError("Current ConstellationSpec has no runtime session identity")
    runtime = load_cr_runtime_config(
        cr.get("spec") or {},
        core_v1=core_v1,
        namespace=namespace,
        source_origin="na-reconfig.live",
        run_id=run_id,
        installed_shipped_root=installed_shipped_root,
    )
    return runtime.resolution


def _selected_resolution(
    session_path: str | None,
    resolution: SessionResolution | None,
    *,
    installed_shipped_root: str | Path = "catalog/nodalarc",
) -> SessionResolution:
    if resolution is not None:
        return resolution
    if not session_path:
        raise ValueError("session_path is required when no live resolution is supplied")
    return load_prepared_tree_session_resolution(
        session_path,
        installed_shipped_root=installed_shipped_root,
        origin="na-reconfig.offline",
    )


def _parse_flow_spec(spec: str) -> dict:
    """Parse flow spec string: flow_id:src:dst:protocol:bandwidth_kbps:probe_type"""
    parts = spec.split(":")
    if len(parts) != 6:
        raise ValueError(
            f"Flow spec must be flow_id:src:dst:protocol:bandwidth_kbps:probe_type, got: {spec}"
        )
    return {
        "flow_id": parts[0],
        "src": parts[1],
        "dst": parts[2],
        "protocol": parts[3],
        "bandwidth_kbps": float(parts[4]),
        "probe_type": parts[5],
    }


def add_flow(
    session_path: str | None,
    flow_spec: str,
    *,
    resolution: SessionResolution | None = None,
    installed_shipped_root: str | Path = "catalog/nodalarc",
) -> None:
    """Add a probe flow to a running session.

    Configures the probe daemon on the source GS pod directly and
    records the flow in the session database.
    """
    resolution = _selected_resolution(
        session_path,
        resolution,
        installed_shipped_root=installed_shipped_root,
    )
    resolved = resolution.resolved

    spec = _parse_flow_spec(flow_spec)
    from measurement import probe_client
    from measurement.flow_manager import ProbeFlowConfig, resolve_dst_ip, resolve_src_pod_ip

    flow = ProbeFlowConfig(**spec)
    dst_ip = resolve_dst_ip(flow.dst, resolved)
    src_pod_ip = resolve_src_pod_ip(flow.src)
    if src_pod_ip is None:
        log.error(f"Cannot resolve pod IP for {flow.src}")
        sys.exit(1)

    probe_client.configure_flow(
        pod_ip=src_pod_ip,
        flow_id=flow.flow_id,
        dst_ip=dst_ip,
        protocol=flow.protocol,
        bandwidth_kbps=flow.bandwidth_kbps,
        probe_type=flow.probe_type,
    )
    log.info(f"Added flow {flow.flow_id}: {flow.src} -> {flow.dst} ({dst_ip})")


def remove_flow(
    session_path: str | None,
    flow_id: str,
    *,
    resolution: SessionResolution | None = None,
    installed_shipped_root: str | Path = "catalog/nodalarc",
) -> None:
    """Remove a probe flow from a running session."""
    resolution = _selected_resolution(
        session_path,
        resolution,
        installed_shipped_root=installed_shipped_root,
    )
    resolved = resolution.resolved

    # We need to find which GS pod this flow runs on.
    # Check all GS pods for the flow.
    from measurement import probe_client
    from measurement.flow_manager import resolve_src_pod_ip

    for node in resolved.nodes:
        if node.kind != "ground_station":
            continue
        pod_ip = resolve_src_pod_ip(node.node_id)
        if pod_ip is None:
            continue
        try:
            probe_client.delete_flow(pod_ip, flow_id)
            log.info(f"Removed flow {flow_id} from {node.node_id}")
            return
        except Exception:
            continue

    log.warning(f"Flow {flow_id} not found on any GS pod")


def main() -> None:
    _configure_logging("nodal.arc.tools.na_reconfig", nats_level=None, stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Nodal Arc probe flow tool")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--live",
        action="store_true",
        help="Use the current ConstellationSpec and its exact catalog upload",
    )
    source.add_argument(
        "--session",
        help="Offline session YAML path, with an optional adjacent prepared catalog tree",
    )
    parser.add_argument("--namespace", default="nodalarc")
    parser.add_argument("--installed-shipped-root", default="catalog/nodalarc")
    parser.add_argument(
        "--add-flow", help="Add probe flow: flow_id:src:dst:protocol:bandwidth_kbps:probe_type"
    )
    parser.add_argument("--remove-flow", help="Remove probe flow by flow_id")
    args = parser.parse_args()
    live_resolution = (
        load_live_session_resolution(
            namespace=args.namespace,
            installed_shipped_root=args.installed_shipped_root,
        )
        if args.live
        else None
    )

    if args.add_flow:
        add_flow(
            args.session,
            args.add_flow,
            resolution=live_resolution,
            installed_shipped_root=args.installed_shipped_root,
        )
    elif args.remove_flow:
        remove_flow(
            args.session,
            args.remove_flow,
            resolution=live_resolution,
            installed_shipped_root=args.installed_shipped_root,
        )
    else:
        parser.error("One of --add-flow or --remove-flow is required")


if __name__ == "__main__":
    main()
