"""Unit tests for probe daemon — flow state management and result tracking."""

from measurement.probe_daemon import (
    PROBE_BIND_HOST_ENV,
    FlowConfig,
    _flows,
    _FlowState,
    _lock,
    _probe_bind_host,
)


def _clear_flows():
    """Clear flow registry."""
    with _lock:
        for state in _flows.values():
            state.stop()
        _flows.clear()


class TestProbeBindHost:
    """Probe listeners bind to a concrete interface, not every interface."""

    def test_default_bind_host_is_loopback(self, monkeypatch):
        monkeypatch.delenv(PROBE_BIND_HOST_ENV, raising=False)

        assert _probe_bind_host() == "127.0.0.1"

    def test_cluster_bind_host_comes_from_pod_ip_env(self, monkeypatch):
        monkeypatch.setenv(PROBE_BIND_HOST_ENV, "10.42.0.17")

        assert _probe_bind_host() == "10.42.0.17"

    def test_blank_bind_host_falls_back_to_loopback(self, monkeypatch):
        monkeypatch.setenv(PROBE_BIND_HOST_ENV, "  ")

        assert _probe_bind_host() == "127.0.0.1"


class TestFlowState:
    """Test internal flow state management."""

    def setup_method(self):
        _clear_flows()

    def teardown_method(self):
        _clear_flows()

    def test_flow_state_creation(self):
        config = FlowConfig(
            flow_id="test1",
            dst_ip="10.0.0.1",
            probe_type="burst",
        )
        state = _FlowState(config)
        assert state.active is True
        assert state.packets_sent == 0
        assert state.packets_received == 0
        assert state.latencies == []

    def test_flow_state_stop(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        state = _FlowState(config)
        state.stop()
        assert state.active is False

    def test_drain_results_empty(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        state = _FlowState(config)
        results = state.drain_results()
        assert results.packets_sent == 0
        assert results.packets_received == 0
        assert results.latency_min_ms == 0.0
        assert results.latency_avg_ms == 0.0

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

    def test_jitter_calculation(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        state = _FlowState(config)
        state.packets_sent = 4
        state.packets_received = 4
        state.latencies = [1.0, 3.0, 2.0, 4.0]

        results = state.drain_results()
        # Jitter = mean of |lat[i] - lat[i-1]| = (2 + 1 + 2) / 3 ≈ 1.667
        assert results.jitter_ms > 0

    def test_flow_registry_add_remove(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1", probe_type="burst")
        with _lock:
            _flows["test1"] = _FlowState(config)
            assert "test1" in _flows

        with _lock:
            state = _flows.pop("test1")
            state.stop()
            assert "test1" not in _flows

    def test_duplicate_flow_detection(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1", probe_type="burst")
        with _lock:
            _flows["test1"] = _FlowState(config)

        with _lock:
            assert "test1" in _flows

    def test_flow_config_defaults(self):
        config = FlowConfig(flow_id="test1", dst_ip="10.0.0.1")
        assert config.protocol == "udp"  # PRD: custom UDP probes
        assert config.bandwidth_kbps == 100
        assert config.probe_type == "continuous"
        assert config.interval_ms == 1000

    def test_probe_packet_format(self):
        """Verify UDP probe packet format: 8-byte seq + 8-byte timestamp."""
        import struct

        from measurement.probe_daemon import PROBE_PACKET_FMT, PROBE_PACKET_SIZE

        assert PROBE_PACKET_SIZE == 16
        # Pack and unpack round-trip
        seq, ts = 42, 1234567890123456
        packed = struct.pack(PROBE_PACKET_FMT, seq, ts)
        assert len(packed) == 16
        unpacked_seq, unpacked_ts = struct.unpack(PROBE_PACKET_FMT, packed)
        assert unpacked_seq == seq
        assert unpacked_ts == ts
