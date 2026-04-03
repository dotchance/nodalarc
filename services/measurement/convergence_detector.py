# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Convergence detector — measures network convergence after link events.

Determines which flows are affected by a link event, drives probe bursts
via probe daemon REST API, and evaluates probe results against stability
criteria.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.models.metrics import ConvergenceResult
from nodalarc.models.session import ConvergenceConfig

log = logging.getLogger(__name__)


def measure_convergence(
    event_id: str,
    link_event: LinkUp | LinkDown,
    convergence_config: ConvergenceConfig,
    active_flows: dict[str, dict[str, Any]],
    adapter: Any | None = None,
    probe_client_mod: Any | None = None,
) -> ConvergenceResult:
    """Measure convergence after a link event.

    Args:
        event_id: Unique event identifier
        link_event: The link event that triggered convergence measurement
        convergence_config: Stability and timeout configuration
        active_flows: Dict of active flow info from FlowManager
        adapter: Protocol adapter for trace_path (optional)
        probe_client_mod: probe_client module for HTTP calls (optional)

    Returns:
        ConvergenceResult with timing and packet stats
    """
    start_time = datetime.now(UTC)
    start_wall = time.monotonic()

    # No flows configured: fixed dwell period, return converged with no measurement
    if not active_flows:
        log.info(
            f"Convergence {event_id}: no flows configured, dwell {convergence_config.timeout_s}s"
        )
        # No flows = no probes to measure. Return immediately as converged.
        end_time = datetime.now(UTC)
        return ConvergenceResult(
            event_id=event_id,
            converged=True,
            duration_ms=0.0,
            packets_lost=0,
            packets_sent=0,
            sim_time_start=start_time,
            sim_time_end=end_time,
            wall_time_start=start_time,
            wall_time_end=end_time,
        )

    # Determine which flows might be affected
    affected_flows = _find_affected_flows(
        link_event,
        active_flows,
        adapter,
    )

    if not affected_flows:
        log.info(f"Convergence {event_id}: no flows affected by link event")
        end_time = datetime.now(UTC)
        return ConvergenceResult(
            event_id=event_id,
            converged=True,
            duration_ms=0.0,
            packets_lost=0,
            packets_sent=0,
            sim_time_start=start_time,
            sim_time_end=end_time,
            wall_time_start=start_time,
            wall_time_end=end_time,
        )

    # Probe affected flows until stability or timeout
    if probe_client_mod is None:
        from measurement import probe_client as probe_client_mod

    timeout_s = convergence_config.timeout_s
    stability_s = convergence_config.stability_period_s
    probe_interval_s = convergence_config.probe_interval_ms / 1000.0

    total_sent = 0
    total_lost = 0
    stable_since: float | None = None
    consecutive_zero_received = 0
    # Fast-fail after N consecutive rounds with zero packets received across
    # all flows.  This avoids blocking 30s when routing is completely down
    # (e.g. initial bringup before IS-IS converges).
    max_zero_rounds = 3

    while True:
        elapsed = time.monotonic() - start_wall

        if elapsed >= timeout_s:
            log.warning(f"Convergence {event_id}: timeout after {elapsed:.1f}s")
            end_time = datetime.now(UTC)
            return ConvergenceResult(
                event_id=event_id,
                converged=False,
                duration_ms=elapsed * 1000,
                packets_lost=total_lost,
                packets_sent=total_sent,
                sim_time_start=start_time,
                sim_time_end=end_time,
                wall_time_start=start_time,
                wall_time_end=end_time,
            )

        # Run probe burst on each affected flow
        all_success = True
        round_received = 0
        for flow_id, flow_info in affected_flows.items():
            try:
                result = probe_client_mod.burst(
                    flow_info["src_pod_ip"],
                    flow_id,
                    count=5,
                    interval_ms=int(probe_interval_s * 1000),
                )
                sent = result.get("packets_sent", 0) if isinstance(result, dict) else 0
                received = result.get("packets_received", 0) if isinstance(result, dict) else 0
                total_sent += sent
                total_lost += sent - received
                round_received += received
                if received < sent:
                    all_success = False
            except Exception as exc:
                log.warning(f"Probe burst failed for {flow_id}: {exc}")
                all_success = False
                total_sent += 5
                total_lost += 5

        # Fast-fail: if zero packets received across all flows for N
        # consecutive rounds, routing is completely non-functional.
        if round_received == 0:
            consecutive_zero_received += 1
            if consecutive_zero_received >= max_zero_rounds:
                duration = time.monotonic() - start_wall
                log.info(
                    f"Convergence {event_id}: no connectivity after "
                    f"{consecutive_zero_received} rounds, fast-fail "
                    f"after {duration:.1f}s"
                )
                end_time = datetime.now(UTC)
                return ConvergenceResult(
                    event_id=event_id,
                    converged=False,
                    duration_ms=duration * 1000,
                    packets_lost=total_lost,
                    packets_sent=total_sent,
                    sim_time_start=start_time,
                    sim_time_end=end_time,
                    wall_time_start=start_time,
                    wall_time_end=end_time,
                )
        else:
            consecutive_zero_received = 0

        now = time.monotonic()
        if all_success:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stability_s:
                duration = now - start_wall
                log.info(f"Convergence {event_id}: converged after {duration:.1f}s")
                end_time = datetime.now(UTC)
                return ConvergenceResult(
                    event_id=event_id,
                    converged=True,
                    duration_ms=duration * 1000,
                    packets_lost=total_lost,
                    packets_sent=total_sent,
                    sim_time_start=start_time,
                    sim_time_end=end_time,
                    wall_time_start=start_time,
                    wall_time_end=end_time,
                )
        else:
            stable_since = None

        time.sleep(probe_interval_s)


def _find_affected_flows(
    link_event: LinkUp | LinkDown,
    active_flows: dict[str, dict[str, Any]],
    adapter: Any | None,
) -> dict[str, dict[str, Any]]:
    """Determine which flows traverse the affected link.

    If adapter supports trace_path, use it to check if the link's
    endpoints appear in any flow's path. Otherwise, conservatively
    assume all flows are affected.
    """
    if adapter is None:
        # No adapter — all flows potentially affected
        return dict(active_flows)

    node_a = link_event.node_a
    node_b = link_event.node_b
    affected: dict[str, dict[str, Any]] = {}

    for flow_id, info in active_flows.items():
        try:
            hops = adapter.trace_path(info["src"], info.get("dst_ip", ""))
            if node_a in hops or node_b in hops:
                affected[flow_id] = info
        except Exception:
            # If trace fails, conservatively include the flow
            affected[flow_id] = info

    return affected if affected else dict(active_flows)
