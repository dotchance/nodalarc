"""Unit tests for probe daemon — flow state management and result tracking."""

from measurement.probe_daemon import (
    FlowConfig,
    _flows,
    _FlowState,
    _lock,
)


def _clear_flows():
    """Clear flow registry."""
    with _lock:
        for state in _flows.values():
            state.stop()
        _flows.clear()


class TestFlowState:
    """Test internal flow state management."""

    def setup_method(self):
        _clear_flows()

    def teardown_method(self):
        _clear_flows()

    def test_drain_results_with_data(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        state = _FlowState(config)
        state.packets_sent = 10
        state.packets_received = 8
        state.latencies = [1.0, 2.0, 3.0, 4.0, 5.0]

        results = state.drain_results()
        assert results.flow_id == "test1"
        assert results.packets_sent == 10
        assert results.packets_received == 8
        assert results.latency_min_ms == 1.0
        assert results.latency_max_ms == 5.0
        assert results.latency_avg_ms == 3.0

    def test_drain_results_resets(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        state = _FlowState(config)
        state.packets_sent = 10
        state.packets_received = 8
        state.latencies = [1.0, 2.0, 3.0]

        state.drain_results()
        # Second drain should be empty
        results = state.drain_results()
        assert results.packets_sent == 0
        assert results.packets_received == 0
