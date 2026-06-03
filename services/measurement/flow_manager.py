# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Flow lifecycle manager — manages active probe flows.

Loads initial flows from the resolver's runtime session projection, resolves
destination IPs from terrestrial prefixes, and configures probe daemons on
source GS pods via probe_client.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import SessionConfig, TrafficFlowConfig

log = logging.getLogger(__name__)


def resolve_dst_ip(
    dst_node_id: str,
    gs_file: GroundStationFile,
    session: SessionConfig,
    addressing: AddressingScheme | None = None,
) -> str:
    """Resolve destination node ID to first IPv4 from terrestrial prefix.

    Uses the first IPv4 address from the destination ground station's
    terrestrial prefix.
    """
    addressing = addressing or AddressingScheme(session.addressing, gs_file=gs_file)

    for i, station in enumerate(gs_file.stations):
        if addressing.gs_id(station.name) == dst_node_id:
            # Check per-station terrestrial prefixes first
            if station.terrestrial_prefixes:
                for tp in station.terrestrial_prefixes:
                    net = ipaddress.ip_network(tp.prefix, strict=False)
                    if net.version == 4:
                        return str(net.network_address + 1)

            # Fall back to default template
            tpl = gs_file.default_terrestrial_prefixes
            if tpl:
                prefix_str = tpl.ipv4_template.format(gs_index=i)
                net = ipaddress.ip_network(prefix_str, strict=False)
                return str(net.network_address + 1)

    raise ValueError(f"Cannot resolve terrestrial IP for {dst_node_id}")


def resolve_src_pod_ip(
    src_node_id: str,
    namespace: str | None = None,
) -> str | None:
    """Resolve source GS node ID to pod IP via kubectl.

    Returns the pod's cluster IP for probe daemon HTTP access.
    """
    if namespace is None:
        from nodalarc.platform_config import get_platform_config

        namespace = get_platform_config().kubernetes_namespace
    import subprocess

    pod_name = src_node_id.lower()
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.podIP}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as exc:
        log.warning(f"Failed to resolve pod IP for {src_node_id}: {exc}")
    return None


class FlowManager:
    """Manages active probe flows across GS pods."""

    def __init__(
        self,
        session: SessionConfig,
        gs_file: GroundStationFile,
        namespace: str | None = None,
        addressing: AddressingScheme | None = None,
    ) -> None:
        if namespace is None:
            from nodalarc.platform_config import get_platform_config

            namespace = get_platform_config().kubernetes_namespace
        self._session = session
        self._gs_file = gs_file
        self._namespace = namespace
        self._addressing = addressing or AddressingScheme(session.addressing, gs_file=gs_file)
        self._active_flows: dict[str, dict[str, Any]] = {}

    @property
    def active_flows(self) -> dict[str, dict[str, Any]]:
        return dict(self._active_flows)

    def load_initial_flows(self) -> None:
        """Load and configure flows from session config."""
        if not self._session.traffic_flows:
            log.info("No traffic flows configured in session")
            return

        for flow_config in self._session.traffic_flows:
            try:
                self.add_flow(flow_config)
            except Exception as exc:
                log.error(f"Failed to configure flow {flow_config.flow_id}: {exc}")

    def add_flow(self, flow_config: TrafficFlowConfig) -> None:
        """Add and activate a probe flow."""
        from measurement import probe_client

        dst_ip = resolve_dst_ip(
            flow_config.dst,
            self._gs_file,
            self._session,
            self._addressing,
        )
        src_pod_ip = resolve_src_pod_ip(
            flow_config.src,
            self._namespace,
        )
        if src_pod_ip is None:
            raise RuntimeError(f"Cannot resolve pod IP for source {flow_config.src}")

        probe_client.configure_flow(
            pod_ip=src_pod_ip,
            flow_id=flow_config.flow_id,
            dst_ip=dst_ip,
            protocol=flow_config.protocol,
            bandwidth_kbps=flow_config.bandwidth_kbps,
            probe_type=flow_config.probe_type,
        )

        self._active_flows[flow_config.flow_id] = {
            "src": flow_config.src,
            "dst": flow_config.dst,
            "dst_ip": dst_ip,
            "src_pod_ip": src_pod_ip,
            "protocol": flow_config.protocol,
            "probe_type": flow_config.probe_type,
        }
        log.info(f"Flow {flow_config.flow_id}: {flow_config.src} → {flow_config.dst} ({dst_ip})")

    def remove_flow(self, flow_id: str) -> None:
        """Stop and remove a probe flow."""
        from measurement import probe_client

        info = self._active_flows.pop(flow_id, None)
        if info is None:
            log.warning(f"Flow {flow_id} not found")
            return

        try:
            probe_client.delete_flow(info["src_pod_ip"], flow_id)
        except Exception as exc:
            log.warning(f"Failed to delete flow {flow_id} from probe daemon: {exc}")

        log.info(f"Removed flow {flow_id}")

    def get_flow_info(self, flow_id: str) -> dict[str, Any] | None:
        """Get info about an active flow."""
        return self._active_flows.get(flow_id)

    def collect_results(self) -> list[dict[str, Any]]:
        """Collect results from all active flows."""
        from measurement import probe_client

        results = []
        for flow_id, info in self._active_flows.items():
            try:
                result = probe_client.get_results(info["src_pod_ip"], flow_id)
                if result:
                    result["flow_id"] = flow_id
                    result["src_node"] = info["src"]
                    result["dst_node"] = info["dst"]
                    results.append(result)
            except Exception as exc:
                log.warning(f"Failed to collect results for flow {flow_id}: {exc}")
        return results
