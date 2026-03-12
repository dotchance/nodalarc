"""Tests for NodalPathConfig."""

from __future__ import annotations

from pathlib import Path

from nodalpath.config import NodalPathConfig


class TestNodalPathConfig:
    def test_defaults_populated_from_zmq_channels(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert "5560" in config.ome_connect
        assert "5561" in config.to_connect
        assert "5567" in config.events_bind

    def test_explicit_overrides_respected(self):
        config = NodalPathConfig(
            session_path=Path("/tmp/test.yaml"),
            ome_connect="tcp://10.0.0.1:9000",
            to_connect="tcp://10.0.0.1:9001",
            events_bind="tcp://10.0.0.1:9002",
        )
        assert config.ome_connect == "tcp://10.0.0.1:9000"
        assert config.to_connect == "tcp://10.0.0.1:9001"
        assert config.events_bind == "tcp://10.0.0.1:9002"

    def test_dry_run_default_false(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.dry_run is False

    def test_transport_default_grpc(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.transport == "grpc"

    def test_mode_default_live(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.mode == "live"

    def test_post_init_sets_zmq_addresses(self):
        from nodalarc.zmq_channels import (
            nodalpath_events_bind,
            ome_events_connect,
            to_events_connect,
        )

        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.ome_connect == ome_events_connect()
        assert config.to_connect == to_events_connect()
        assert config.events_bind == nodalpath_events_bind()
