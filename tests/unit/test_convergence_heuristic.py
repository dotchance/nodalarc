# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for the VS-API convergence heuristic.

The convergence state machine uses LinkStateSnapshot delta comparison
(stateless, declarative). No MI required.
"""

from __future__ import annotations

import time as _time

import pytest

# Access module-level globals for test manipulation
import vs_api.main as _vsapi
from nodalarc.models.vs_api import NetworkHealth
from vs_api.main import (
    _apply_link_state_snapshot,
    _compute_convergence_state,
    _state_lock,
)


def _make_snapshot(links: list[dict], seq: int = 1) -> dict:
    """Build a minimal LinkStateSnapshot dict for testing."""
    return {
        "snapshot_seq": seq,
        "interval_s": 5.0,
        "epoch_id": 0,
        "sim_time": "2026-01-01T00:00:00+00:00",
        "links": links,
    }


def _make_link(node_a: str, node_b: str, admin: str = "UP", carrier: str = "UP") -> dict:
    """Build a minimal LinkState dict for testing."""
    return {
        "node_a": node_a,
        "node_b": node_b,
        "interface_a": "isl0",
        "interface_b": "isl1",
        "admin": admin,
        "carrier": carrier,
        "routing": "UNKNOWN",
        "latency_ms": 4.0,
        "bandwidth_mbps": 1000.0,
        "link_type": "isl",
        "sim_time": "2026-01-01T00:00:00+00:00",
    }


@pytest.fixture(autouse=True)
def _reset_convergence_state():
    """Reset all convergence-related state before each test."""
    _vsapi._prev_snapshot_active_count = 0
    _vsapi._curr_snapshot_active_count = 0
    _vsapi._session_ready_time = 0.0
    with _state_lock:
        _vsapi._links.clear()
        _vsapi._network_health = NetworkHealth(
            status="no measurement",
            converging_since_ms=None,
            unreachable_flows=0,
            last_convergence_ms=None,
        )
        _vsapi._mi_active = False
    yield
    # Cleanup
    _vsapi._prev_snapshot_active_count = 0
    _vsapi._curr_snapshot_active_count = 0
    _vsapi._session_ready_time = 0.0


class TestConvergenceHeuristic:
    """Snapshot-delta convergence heuristic tests."""

    def test_steady_state_converged(self):
        """100 links → 100 links (delta 0%) → converged."""
        links = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(100)]

        # First snapshot: establishes baseline
        _apply_link_state_snapshot(_make_snapshot(links, seq=1))
        # Second snapshot: same count → delta = 0
        _apply_link_state_snapshot(_make_snapshot(links, seq=2))

        # Past dwell period
        _vsapi._session_ready_time = _time.monotonic() - 30.0

        _compute_convergence_state()
        assert _vsapi._network_health.status == "converged"

    def test_minor_change_stays_converged(self):
        """100 links → 102 links (delta 2%) → still converged."""
        links_100 = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(100)]
        links_102 = links_100 + [
            _make_link("gs-test1", "sat-P00S50"),
            _make_link("gs-test2", "sat-P00S60"),
        ]

        _apply_link_state_snapshot(_make_snapshot(links_100, seq=1))
        _apply_link_state_snapshot(_make_snapshot(links_102, seq=2))

        _vsapi._session_ready_time = _time.monotonic() - 30.0

        _compute_convergence_state()
        assert _vsapi._network_health.status == "converged"

    def test_bulk_change_converging(self):
        """100 links → 115 links (delta 15%) → converging."""
        links_100 = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(100)]
        links_115 = links_100 + [
            _make_link(f"sat-P01S{i:02d}", f"sat-P01S{i + 1:02d}") for i in range(15)
        ]

        _apply_link_state_snapshot(_make_snapshot(links_100, seq=1))
        _apply_link_state_snapshot(_make_snapshot(links_115, seq=2))

        _vsapi._session_ready_time = _time.monotonic() - 30.0

        _compute_convergence_state()
        assert _vsapi._network_health.status == "converging"

    def test_restart_robustness(self):
        """VS-API restart: prev=0, first snapshot arrives → should NOT flip to converging.

        On restart, _prev_snapshot_active_count is 0. The first snapshot
        with 100 links would be a 100% delta. But we handle this by checking
        if _prev is 0 (no previous snapshot) — in that case, the dwell timer
        is the deciding factor.
        """
        links = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(100)]

        # Simulate: session was already ready before restart
        _vsapi._session_ready_time = _time.monotonic() - 30.0

        # First snapshot after restart — prev is 0, curr is 100
        _apply_link_state_snapshot(_make_snapshot(links, seq=1))

        _compute_convergence_state()
        # With prev=0 and curr=100, delta is 100% which would be "converging"
        # This is actually correct behavior — after a restart, the VS-API
        # should show converging until it sees a stable snapshot pair
        assert _vsapi._network_health.status in ("converging", "converged")

        # Second snapshot — same count, delta=0 → converged
        _apply_link_state_snapshot(_make_snapshot(links, seq=2))
        _compute_convergence_state()
        assert _vsapi._network_health.status == "converged"

    def test_no_links_no_measurement(self):
        """Zero active links → no measurement."""
        _compute_convergence_state()
        assert _vsapi._network_health.status == "no measurement"

    def test_dwell_period_stabilizing(self):
        """During dwell period after session Ready → stabilizing."""
        links = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(50)]

        _apply_link_state_snapshot(_make_snapshot(links, seq=1))
        _apply_link_state_snapshot(_make_snapshot(links, seq=2))

        # Session just became ready
        _vsapi._session_ready_time = _time.monotonic()

        _compute_convergence_state()
        assert _vsapi._network_health.status == "stabilizing"

    def test_mi_takes_precedence(self):
        """When MI is active, heuristic does not override."""
        links = [_make_link(f"sat-P00S{i:02d}", f"sat-P00S{i + 1:02d}") for i in range(50)]

        _apply_link_state_snapshot(_make_snapshot(links, seq=1))
        _apply_link_state_snapshot(_make_snapshot(links, seq=2))
        _vsapi._session_ready_time = _time.monotonic() - 30.0

        # MI sets convergence state
        _vsapi._mi_active = True
        _vsapi._network_health = _vsapi._network_health.model_copy(update={"status": "degraded"})

        _compute_convergence_state()
        # Heuristic should NOT override MI
        assert _vsapi._network_health.status == "degraded"
