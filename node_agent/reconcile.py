"""Kernel-vs-ConfigMap reconciliation for Node Agent wiring.

PRD v0.38 Node Agent Reconciliation Model: the Node Agent is stateless
across restarts. On every startup and ConfigMap change, it diffs desired
(ConfigMap) vs actual (kernel) and acts accordingly:
  Case A — No kernel state, no current wiring-status: wire from scratch
  Case B — Wiring-status present and current: no-op
  Case C — Kernel state exists but wiring-status absent/stale: clean, re-wire
"""

from __future__ import annotations

import logging

from pyroute2 import IPRoute

log = logging.getLogger(__name__)

_PATTERNS = ("_isl_", "_gnd_", "_gbr-", "br-gnd-")


def get_actual_nodalarc_interfaces() -> set[str]:
    """Enumerate nodalarc host-side interfaces from kernel via pyroute2."""
    with IPRoute() as ipr:
        return {
            link.get_attr("IFLA_IFNAME", "")
            for link in ipr.get_links()
            if any(p in link.get_attr("IFLA_IFNAME", "") for p in _PATTERNS)
        }


def clean_nodalarc_kernel_state() -> int:
    """Remove all nodalarc host-side interfaces. Returns count removed."""
    removed = 0
    with IPRoute() as ipr:
        for link in ipr.get_links():
            name = link.get_attr("IFLA_IFNAME", "")
            if any(p in name for p in _PATTERNS):
                try:
                    ipr.link("del", index=link["index"])
                    removed += 1
                except Exception:
                    pass  # already gone
    return removed


def wiring_status_is_current(
    v1,
    namespace: str,
    manifest_nodes: dict,
) -> bool:
    """Check if nodalarc-wiring-status reflects the current manifest.

    Returns True (Case B) if wiring-status exists and its node set
    covers all nodes in the manifest. Returns False otherwise.
    """
    try:
        cm = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
        if not cm.data:
            return False
        wired_nodes = set(cm.data.keys())
        expected_nodes = set(manifest_nodes.keys())
        return expected_nodes.issubset(wired_nodes)
    except Exception:
        return False
