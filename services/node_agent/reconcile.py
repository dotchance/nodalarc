# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel-vs-ConfigMap reconciliation for Node Agent wiring.

The Node Agent is stateless across restarts. On every startup and ConfigMap
change, it diffs desired (ConfigMap) vs actual (kernel) and acts accordingly:
  Case A — No kernel state, no current wiring-status: wire from scratch
  Case B — Wiring-status present and current: no-op
  Case C — Kernel state exists but wiring-status absent/stale: clean, re-wire
"""

from __future__ import annotations

import logging

from nodalarc.runtime_naming import is_managed_host_ifname
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.wiring_status import parse_status_configmap
from pyroute2 import IPRoute

log = logging.getLogger(__name__)


def get_actual_nodalarc_interfaces() -> set[str]:
    """Enumerate nodalarc host-side interfaces from kernel via pyroute2."""
    with IPRoute() as ipr:
        return {
            link.get_attr("IFLA_IFNAME", "")
            for link in ipr.get_links()
            if is_managed_host_ifname(link.get_attr("IFLA_IFNAME", ""))
        }


def clean_nodalarc_kernel_state() -> int:
    """Remove all nodalarc host-side interfaces. Returns count removed."""
    removed = 0
    with IPRoute() as ipr:
        for link in ipr.get_links():
            name = link.get_attr("IFLA_IFNAME", "")
            if is_managed_host_ifname(name):
                try:
                    ipr.link("del", index=link["index"])
                    removed += 1
                except Exception:
                    pass  # already gone
    return removed


def wiring_status_is_current(
    v1,
    namespace: str,
    manifest: WiringManifest,
) -> bool:
    """Check if nodalarc-wiring-status reflects the current manifest.

    Returns True (Case B) if wiring-status exists, matches session and
    generation, and every manifest node has a ready phase-complete status.
    """
    try:
        cm = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
        if not cm.data:
            return False
        session_id, generation, statuses = parse_status_configmap(cm.data)
        if session_id != manifest.session_id:
            return False
        if generation != manifest.wiring_generation:
            return False
        expected_nodes = set(manifest.nodes.keys())
        if not expected_nodes.issubset(statuses.keys()):
            return False
        return all(statuses[node_id].ready_for(manifest) for node_id in expected_nodes)
    except Exception as exc:
        log.warning("wiring-status validation failed: %s", exc)
        return False
