"""Unit tests for convergence detector (PRD Appendix B line 2147).

Tests:
- Detector identifies which flows traverse a given link
- Declares convergence after stability_period_s of continuous probe success
- Declares timeout after timeout_s
- Handles no-flows-configured case (fixed dwell, no measurement)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from measurement.convergence_detector import (
    measure_convergence,
    _find_affected_flows,
)
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.models.session import ConvergenceConfig


def _make_link_down(node_a="sat-P00S00", node_b="sat-P00S01"):
    return LinkDown(
        sim_time=datetime.now(timezone.utc),
        wall_time=datetime.now(timezone.utc),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        reason="vis_lost",
    )


def _make_link_up(node_a="sat-P00S00", node_b="sat-P00S01"):
    return LinkUp(
        sim_time=datetime.now(timezone.utc),
        wall_time=datetime.now(timezone.utc),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        latency_ms=10.0,
        bandwidth_mbps=1000,
        reason="vis_gained",
    )


class TestNoFlowsConfigured:
    """Handles no-flows-configured case correctly."""

    def test_no_flows_returns_converged(self):
        config = ConvergenceConfig(
            stability_period_s=2.0,
            timeout_s=30.0,
            probe_interval_ms=100,
        )
        result = measure_convergence(
            event_id="test-001",
            link_event=_make_link_down(),
            convergence_config=config,
            active_flows={},
        )
        assert result.converged is True
        assert result.packets_sent == 0
        assert result.packets_lost == 0
        assert result.duration_ms == 0.0
        assert result.event_id == "test-001"

    def test_no_flows_link_up(self):
        config = ConvergenceConfig()
        result = measure_convergence(
            event_id="test-002",
            link_event=_make_link_up(),
            convergence_config=config,
            active_flows={},
        )
        assert result.converged is True


class TestFindAffectedFlows:
    """Detector identifies which flows traverse a given link."""

    def test_no_adapter_returns_all_flows(self):
        flows = {
            "f1": {"src": "gs-hawthorne", "dst": "gs-frankfurt", "src_pod_ip": "10.42.0.1"},
            "f2": {"src": "gs-frankfurt", "dst": "gs-hawthorne", "src_pod_ip": "10.42.0.2"},
        }
        event = _make_link_down()
        affected = _find_affected_flows(event, flows, adapter=None)
        assert len(affected) == 2

    def test_adapter_filters_by_path(self):
        mock_adapter = MagicMock()
        # Flow f1 traverses the failed link
        mock_adapter.trace_path.side_effect = [
            ["gs-hawthorne", "sat-P00S00", "sat-P00S01", "gs-frankfurt"],
            ["gs-frankfurt", "sat-P01S00", "sat-P01S01", "gs-hawthorne"],
        ]
        flows = {
            "f1": {"src": "gs-hawthorne", "dst": "gs-frankfurt", "dst_ip": "172.16.1.1", "src_pod_ip": "10.42.0.1"},
            "f2": {"src": "gs-frankfurt", "dst": "gs-hawthorne", "dst_ip": "172.16.0.1", "src_pod_ip": "10.42.0.2"},
        }
        event = _make_link_down("sat-P00S00", "sat-P00S01")
        affected = _find_affected_flows(event, flows, adapter=mock_adapter)
        assert "f1" in affected
        assert "f2" not in affected

    def test_adapter_trace_failure_includes_flow(self):
        mock_adapter = MagicMock()
        mock_adapter.trace_path.side_effect = Exception("kubectl failed")
        flows = {
            "f1": {"src": "gs-hawthorne", "dst": "gs-frankfurt", "src_pod_ip": "10.42.0.1"},
        }
        event = _make_link_down()
        affected = _find_affected_flows(event, flows, adapter=mock_adapter)
        assert "f1" in affected


class TestConvergenceWithFlows:
    """Convergence measurement with active flows."""

    def test_immediate_convergence(self):
        """All probes succeed immediately → converge fast."""
        mock_client = MagicMock()
        mock_client.burst.return_value = {
            "packets_sent": 5,
            "packets_received": 5,
        }

        config = ConvergenceConfig(
            stability_period_s=0.1,
            timeout_s=5.0,
            probe_interval_ms=50,
        )
        flows = {
            "f1": {
                "src": "gs-hawthorne",
                "dst": "gs-frankfurt",
                "dst_ip": "172.16.1.1",
                "src_pod_ip": "10.42.0.1",
            },
        }
        result = measure_convergence(
            event_id="test-conv-1",
            link_event=_make_link_down(),
            convergence_config=config,
            active_flows=flows,
            probe_client_mod=mock_client,
        )
        assert result.converged is True
        assert result.packets_lost == 0
        assert result.packets_sent > 0

    def test_timeout_on_failure(self):
        """All probes fail → timeout."""
        mock_client = MagicMock()
        mock_client.burst.return_value = {
            "packets_sent": 5,
            "packets_received": 0,
        }

        config = ConvergenceConfig(
            stability_period_s=0.1,
            timeout_s=0.5,
            probe_interval_ms=50,
        )
        flows = {
            "f1": {
                "src": "gs-hawthorne",
                "dst": "gs-frankfurt",
                "dst_ip": "172.16.1.1",
                "src_pod_ip": "10.42.0.1",
            },
        }
        result = measure_convergence(
            event_id="test-timeout-1",
            link_event=_make_link_down(),
            convergence_config=config,
            active_flows=flows,
            probe_client_mod=mock_client,
        )
        assert result.converged is False
        assert result.packets_lost > 0

    def test_probe_exception_counts_as_loss(self):
        """Probe client exception → counts as packet loss."""
        mock_client = MagicMock()
        mock_client.burst.side_effect = Exception("connection refused")

        config = ConvergenceConfig(
            stability_period_s=0.1,
            timeout_s=0.3,
            probe_interval_ms=50,
        )
        flows = {
            "f1": {
                "src": "gs-hawthorne",
                "dst": "gs-frankfurt",
                "dst_ip": "172.16.1.1",
                "src_pod_ip": "10.42.0.1",
            },
        }
        result = measure_convergence(
            event_id="test-exc-1",
            link_event=_make_link_down(),
            convergence_config=config,
            active_flows=flows,
            probe_client_mod=mock_client,
        )
        assert result.converged is False
        assert result.packets_lost > 0
