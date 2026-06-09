# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Flow lifecycle manager for MI probe traffic.

Flow resolution reads the resolved catalog runtime view. It does not rebuild
ground-station files or session addressing templates.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Literal

from nodalarc.models.resolved_session import ResolvedNode, ResolvedSession

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbeFlowConfig:
    """One active probe flow configured by MI."""

    flow_id: str
    src: str
    dst: str
    protocol: Literal["udp", "tcp"]
    bandwidth_kbps: float
    probe_type: Literal["continuous", "burst"]

    def __post_init__(self) -> None:
        for field_name in ("flow_id", "src", "dst"):
            if not getattr(self, field_name):
                raise ValueError(f"probe flow {field_name} is required")
        if self.src == self.dst:
            raise ValueError("probe flow src and dst must differ")
        if self.bandwidth_kbps <= 0:
            raise ValueError("probe flow bandwidth_kbps must be > 0")


def resolve_dst_ip(dst_node_id: str, resolved: ResolvedSession) -> str:
    """Resolve a destination node to an IPv4 address for probe traffic.

    Prefer explicitly originated non-default IPv4 prefixes because those model
    routed customer/LAN reachability. If none exist, fall back to the resolved
    `terr0` interface address for node-reachability probes. There is no old
    default prefix template and no invented address.
    """
    if not isinstance(resolved, ResolvedSession):
        raise TypeError("resolve_dst_ip requires a ResolvedSession")
    node = resolved.node_by_id(dst_node_id)
    if node is None:
        raise ValueError(f"Cannot resolve probe destination {dst_node_id!r}: unknown node")
    if node.kind != "ground_station":
        raise ValueError(
            f"Cannot resolve probe destination {dst_node_id!r}: destination is not a ground node"
        )

    for prefix in _originated_ipv4_prefixes(node):
        net = ipaddress.ip_network(prefix, strict=False)
        if net.prefixlen == 0:
            continue
        if net.prefixlen == 32:
            return str(net.network_address)
        return str(net.network_address + 1)

    if node.interfaces is not None and node.interfaces.terr0 is not None:
        terr0 = node.interfaces.terr0.ipv4
        if terr0:
            return str(ipaddress.ip_interface(terr0).ip)

    raise ValueError(
        f"Cannot resolve probe destination {dst_node_id!r}: no originated IPv4 prefix "
        "or terr0 IPv4 address"
    )


def resolve_src_pod_ip(
    src_node_id: str,
    namespace: str | None = None,
) -> str | None:
    """Resolve source node ID to pod IP via kubectl."""
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
        log.warning("Failed to resolve pod IP for %s: %s", src_node_id, exc)
    return None


class FlowManager:
    """Manages active probe flows across ground-node pods."""

    def __init__(
        self,
        resolved: ResolvedSession,
        namespace: str | None = None,
        traffic_flows: tuple[ProbeFlowConfig, ...] = (),
    ) -> None:
        if namespace is None:
            from nodalarc.platform_config import get_platform_config

            namespace = get_platform_config().kubernetes_namespace
        if not isinstance(resolved, ResolvedSession):
            raise TypeError("FlowManager requires a ResolvedSession")
        self._resolved = resolved
        self._namespace = namespace
        self._traffic_flows = traffic_flows
        self._active_flows: dict[str, dict[str, object]] = {}

    @property
    def active_flows(self) -> dict[str, dict[str, object]]:
        return dict(self._active_flows)

    def load_initial_flows(self) -> None:
        """Load and configure flows supplied to MI."""
        if not self._traffic_flows:
            log.info("No traffic flows configured for MI")
            return

        for flow_config in self._traffic_flows:
            try:
                self.add_flow(flow_config)
            except Exception as exc:
                log.error("Failed to configure flow %s: %s", flow_config.flow_id, exc)

    def add_flow(self, flow_config: ProbeFlowConfig) -> None:
        """Add and activate a probe flow."""
        from measurement import probe_client

        dst_ip = resolve_dst_ip(flow_config.dst, self._resolved)
        src_pod_ip = resolve_src_pod_ip(flow_config.src, self._namespace)
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
        log.info(
            "Flow %s: %s -> %s (%s)", flow_config.flow_id, flow_config.src, flow_config.dst, dst_ip
        )

    def remove_flow(self, flow_id: str) -> None:
        """Stop and remove a probe flow."""
        from measurement import probe_client

        info = self._active_flows.pop(flow_id, None)
        if info is None:
            log.warning("Flow %s not found", flow_id)
            return

        try:
            probe_client.delete_flow(str(info["src_pod_ip"]), flow_id)
        except Exception as exc:
            log.warning("Failed to delete flow %s from probe daemon: %s", flow_id, exc)

        log.info("Removed flow %s", flow_id)

    def get_flow_info(self, flow_id: str) -> dict[str, object] | None:
        """Get info about an active flow."""
        return self._active_flows.get(flow_id)

    def collect_results(self) -> list[dict[str, object]]:
        """Collect results from all active flows."""
        from measurement import probe_client

        results: list[dict[str, object]] = []
        for flow_id, info in self._active_flows.items():
            try:
                result = probe_client.get_results(str(info["src_pod_ip"]), flow_id)
                if result:
                    result["flow_id"] = flow_id
                    result["src_node"] = info["src"]
                    result["dst_node"] = info["dst"]
                    results.append(result)
            except Exception as exc:
                log.warning("Failed to collect results for flow %s: %s", flow_id, exc)
        return results


def _originated_ipv4_prefixes(node: ResolvedNode) -> tuple[str, ...]:
    if node.originated_prefixes is None:
        return ()
    return tuple(str(prefix) for prefix in node.originated_prefixes.ipv4)
