# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for the VS-API convergence heuristic.

The convergence state machine uses LinkStateSnapshot delta comparison
(stateless, declarative). No MI required. Tests operate on SessionContext
directly — no module-level globals.
"""

from __future__ import annotations

import time as _time

from vs_api.session_context import (
    SessionContext,
    _link_key,
)


def _make_ctx() -> SessionContext:
    """Create a SessionContext with just state initialized (no NATS)."""
    ctx = SessionContext.__new__(SessionContext)
    ctx._init_state_only()
    return ctx


def _apply_links(ctx: SessionContext, count: int, seq: int = 1, prefix: str = "P00") -> None:
    """Simulate applying a LinkStateSnapshot with `count` active ISL links."""
    with ctx.state_lock:
        ctx.links.clear()
        for i in range(count):
            node_a = f"sat-{prefix}S{i:02d}"
            node_b = f"sat-{prefix}S{i + 1:02d}"
            key = _link_key(node_a, node_b)
            from nodalarc.models.vs_api import LinkState

            ctx.links[key] = LinkState(
                node_a=node_a,
                node_b=node_b,
                state="active",
                link_type="intra_plane_isl",
                link_reason="",
                latency_ms=4.0,
                bandwidth_mbps=1000.0,
                range_km=1200.0,
                traffic_load_pct=None,
                interface_a="isl0",
                interface_b="isl1",
            )
        ctx.prev_snapshot_active_count = ctx.curr_snapshot_active_count
        ctx.curr_snapshot_active_count = len(ctx.links)


class TestConvergenceHeuristic:
    """Snapshot-delta convergence heuristic tests."""

    def test_steady_state_converged(self):
        """100 links → 100 links (delta 0%) → converged."""
        ctx = _make_ctx()
        _apply_links(ctx, 100, seq=1)
        _apply_links(ctx, 100, seq=2)
        ctx.session_ready_time = _time.monotonic() - 30.0
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "converged"

    def test_minor_change_stays_converged(self):
        """100 links → 102 links (delta 2%) → still converged."""
        ctx = _make_ctx()
        _apply_links(ctx, 100, seq=1)
        _apply_links(ctx, 102, seq=2)
        ctx.session_ready_time = _time.monotonic() - 30.0
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "converged"

    def test_bulk_change_converging(self):
        """100 links → 115 links (delta 15%) → converging."""
        ctx = _make_ctx()
        _apply_links(ctx, 100, seq=1)
        _apply_links(ctx, 115, seq=2)
        ctx.session_ready_time = _time.monotonic() - 30.0
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "converging"

    def test_restart_robustness(self):
        """VS-API restart: prev=0, first snapshot → converging until stable pair."""
        ctx = _make_ctx()
        ctx.session_ready_time = _time.monotonic() - 30.0

        _apply_links(ctx, 100, seq=1)
        ctx.compute_convergence_state()
        assert ctx.network_health.status in ("converging", "converged")

        _apply_links(ctx, 100, seq=2)
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "converged"

    def test_no_links_no_measurement(self):
        """Zero active links → no measurement."""
        ctx = _make_ctx()
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "no measurement"

    def test_dwell_period_stabilizing(self):
        """During dwell period after session Ready → stabilizing."""
        ctx = _make_ctx()
        _apply_links(ctx, 50, seq=1)
        _apply_links(ctx, 50, seq=2)
        ctx.session_ready_time = _time.monotonic()
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "stabilizing"

    def test_mi_takes_precedence(self):
        """When MI is active, heuristic does not override."""
        ctx = _make_ctx()
        _apply_links(ctx, 50, seq=1)
        _apply_links(ctx, 50, seq=2)
        ctx.session_ready_time = _time.monotonic() - 30.0
        ctx.mi_active = True
        ctx.network_health = ctx.network_health.model_copy(update={"status": "degraded"})
        ctx.compute_convergence_state()
        assert ctx.network_health.status == "degraded"
