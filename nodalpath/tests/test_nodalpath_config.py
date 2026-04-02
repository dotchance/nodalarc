"""Tests for NodalPathConfig."""

from __future__ import annotations

from pathlib import Path

from nodalpath.config import NodalPathConfig


class TestNodalPathConfig:
    def test_defaults_populated_from_nats_channels(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert "nats://" in config.nats_url

    def test_explicit_nats_url_respected(self):
        config = NodalPathConfig(
            session_path=Path("/tmp/test.yaml"),
            nats_url="nats://10.0.0.1:4222",
        )
        assert config.nats_url == "nats://10.0.0.1:4222"

    def test_dry_run_default_false(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.dry_run is False

    def test_transport_default_grpc(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.transport == "grpc"

    def test_mode_default_live(self):
        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.mode == "live"

    def test_post_init_sets_nats_url(self):
        from nodalarc.nats_channels import nats_url

        config = NodalPathConfig(session_path=Path("/tmp/test.yaml"))
        assert config.nats_url == nats_url()
